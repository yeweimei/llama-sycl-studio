"""服务管理 API - router 模型池管理（替代旧容器管理）"""
import json
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

from app import router_client
from app.database import get_conn, now
from app.config import settings
from app.routers.stats import _record_stats

router = APIRouter()


class ServiceCreate(BaseModel):
    name: str
    model_path: str
    args: dict = {}
    gpu_id: Optional[str] = None


class ServiceUpdate(BaseModel):
    args: Optional[dict] = None
    name: Optional[str] = None
    model_path: Optional[str] = None
    gpu_id: Optional[str] = None


def _extract_proc_info(loaded_detail: dict) -> dict:
    """从 router /models 返回的 loaded_detail 中解析进程级信息"""
    import subprocess
    import re

    info = {"port": None, "device": None, "device_label": None, "pid": None, "loaded_at": None}
    if not loaded_detail:
        return info

    # status.args 是启动参数字符串，如 "--port 8081 --device SYCL1 -c 8192 ..."
    args_str = ""
    status = loaded_detail.get("status")
    if isinstance(status, dict):
        args_val = status.get("args")
        if isinstance(args_val, str):
            args_str = args_val
        elif isinstance(args_val, list):
            args_str = " ".join(str(a) for a in args_val)
    # 也尝试顶层 args 字段
    if not args_str:
        args_val = loaded_detail.get("args")
        if isinstance(args_val, str):
            args_str = args_val
        elif isinstance(args_val, list):
            args_str = " ".join(str(a) for a in args_val)

    # 解析 --port
    m_port = re.search(r"--port\s+(\d+)", args_str)
    if m_port:
        info["port"] = int(m_port.group(1))

    # 解析 --device
    m_dev = re.search(r"--device\s+(\S+)", args_str)
    if m_dev:
        dev = m_dev.group(1)
    else:
        dev = "SYCL0"
    info["device"] = dev
    # 设备标签映射
    if "SYCL0" in dev:
        info["device_label"] = "独显"
    elif "SYCL1" in dev:
        info["device_label"] = "核显"
    else:
        info["device_label"] = dev

    # 通过端口查 PID
    if info["port"]:
        try:
            ps_out = subprocess.run(
                ["ps", "-eo", "pid,args"], capture_output=True, text=True, timeout=5
            ).stdout
            for line in ps_out.splitlines():
                if f"--port {info['port']}" in line and "llama-server" in line:
                    pid_str = line.strip().split()[0]
                    try:
                        info["pid"] = int(pid_str)
                    except ValueError:
                        pass
                    break
        except Exception:
            pass

    # loaded_at: 尝试从 status.created 或顶层字段取
    if isinstance(status, dict):
        created = status.get("created") or status.get("loaded_at")
        if created:
            info["loaded_at"] = created
    if not info["loaded_at"]:
        info["loaded_at"] = loaded_detail.get("created")

    return info


@router.get("")
def list_services():
    """列出模型池：合并 DB 注册的模型 + router 发现的模型（自动注册发现的新模型）"""
    # 从 router 获取实时状态
    router_models = router_client.list_models_sync()
    loaded_info = router_client.get_loaded_models_sync()

    # 构建 loaded 模型的详情映射（router /models 接口，data 数组）
    loaded_map = {}
    if isinstance(loaded_info, list):
        for m in loaded_info:
            mid = m.get("model", m.get("id", ""))
            if mid:
                loaded_map[mid] = m
    elif isinstance(loaded_info, dict):
        for m in loaded_info.get("data", []):
            mid = m.get("model", m.get("id", ""))
            if mid:
                loaded_map[mid] = m

    # 从 DB 获取注册的模型元信息，并自动注册 router 发现的新模型（按 name upsert）
    db_models = {}
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM services ORDER BY id").fetchall()
        for r in rows:
            d = dict(r)
            d["args"] = json.loads(d["args"] or "{}")
            db_models[d["name"]] = d
        # 自动注册 router 发现但 DB 没有的模型
        for rm in router_models:
            mid = rm["id"]
            if mid not in db_models:
                cur = conn.execute(
                    "INSERT INTO services (name, model_path, args, status, created_at, updated_at) "
                    "VALUES (?,?, '{}', 'unloaded', ?, ?)",
                    (mid, f"/models/{mid}.gguf", now(), now()),
                )
                db_models[mid] = {
                    "id": cur.lastrowid, "name": mid, "model_path": f"/models/{mid}.gguf",
                    "args": {}, "status": "unloaded",
                    "created_at": now(), "updated_at": now(),
                }

    # 合并：router 发现的所有模型 + DB 注册的模型
    result = []
    seen = set()

    for rm in router_models:
        mid = rm["id"]
        seen.add(mid)
        db_info = db_models.get(mid, {})
        # 状态：优先 router /models 的 status.value，回退 /v1/models 的 status
        state = rm.get("status", "unloaded")
        if mid in loaded_map:
            st = loaded_map[mid].get("status")
            if isinstance(st, dict):
                state = router_client._parse_status(st.get("value", ""))
            elif isinstance(st, str):
                state = router_client._parse_status(st)
        is_loaded = state == "loaded"
        loaded_detail = loaded_map.get(mid, {})
        proc = _extract_proc_info(loaded_detail) if is_loaded else {"port": None, "device": None, "device_label": None, "pid": None, "loaded_at": None}
        result.append({
            "id": db_info.get("id", 0),
            "name": mid,
            "model_path": db_info.get("model_path", f"/models/{mid}.gguf"),
            "args": db_info.get("args", {}),
            "gpu_id": db_info.get("gpu_id", ""),
            "status": state,
            "loaded": is_loaded,
            "loaded_info": loaded_detail,
            "port": proc["port"],
            "device": proc["device"],
            "device_label": proc["device_label"],
            "pid": proc["pid"],
            "loaded_at": proc["loaded_at"],
            "created_at": db_info.get("created_at"),
            "updated_at": db_info.get("updated_at"),
        })

    # DB 中有但 router 未发现的模型（可能文件不存在）
    for name, db_info in db_models.items():
        if name not in seen:
            result.append({
                "id": db_info["id"],
                "name": name,
                "model_path": db_info["model_path"],
                "args": db_info["args"],
                "gpu_id": db_info.get("gpu_id", ""),
                "status": "unavailable",
                "loaded": False,
                "loaded_info": {},
                "port": None,
                "device": None,
                "device_label": None,
                "pid": None,
                "loaded_at": None,
                "created_at": db_info.get("created_at"),
                "updated_at": db_info.get("updated_at"),
            })

    return result


@router.get("/router/status")
def router_status():
    """获取 router 健康状态和驻留模型详情"""
    healthy = router_client.health_check_sync()
    loaded = router_client.get_loaded_models_sync() if healthy else []
    return {
        "healthy": healthy,
        "router_url": settings.router_url,
        "loaded_models": loaded,
    }


@router.post("")
def create_service(body: ServiceCreate):
    """注册模型到模型池（仅 DB 记录，不加载）"""
    with get_conn() as conn:
        dup = conn.execute("SELECT id FROM services WHERE name=?", (body.name,)).fetchone()
        if dup:
            raise HTTPException(400, f"模型 {body.name} 已注册")
        cur = conn.execute(
            "INSERT INTO services (name, model_path, args, gpu_id, status, created_at, updated_at) "
            "VALUES (?,?,?,?, 'unloaded', ?, ?)",
            (body.name, body.model_path, json.dumps(body.args or {}), body.gpu_id or "", now(), now()),
        )
        sid = cur.lastrowid
    return {"id": sid, "name": body.name, "status": "unloaded"}


@router.get("/{sid}")
def get_service(sid: int):
    """获取单个模型详情"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "模型不存在")
    d = dict(row)
    d["args"] = json.loads(d["args"] or "{}")

    # 查 router 实时状态
    router_models = router_client.list_models_sync()
    loaded_info = router_client.get_loaded_models_sync()
    loaded_map = {}
    if isinstance(loaded_info, list):
        for m in loaded_info:
            mid = m.get("model", m.get("id", ""))
            if mid:
                loaded_map[mid] = m
    elif isinstance(loaded_info, dict):
        for m in loaded_info.get("data", []):
            mid = m.get("model", m.get("id", ""))
            if mid:
                loaded_map[mid] = m

    rm = next((m for m in router_models if m["id"] == d["name"]), None)
    if rm:
        state = rm.get("status", "unloaded")
        if d["name"] in loaded_map:
            st = loaded_map[d["name"]].get("status")
            if isinstance(st, dict):
                state = router_client._parse_status(st.get("value", ""))
            elif isinstance(st, str):
                state = router_client._parse_status(st)
        d["loaded"] = state == "loaded"
        d["status"] = state
        d["loaded_info"] = loaded_map.get(d["name"], {})
    else:
        d["loaded"] = False
        d["status"] = "unavailable"
        d["loaded_info"] = {}

    return d


@router.put("/{sid}")
def update_service(sid: int, body: ServiceUpdate):
    """更新模型配置"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
        if not row:
            raise HTTPException(404, "模型不存在")
        d = dict(row)
        args = json.loads(d["args"] or "{}") if body.args is None else body.args
        name = d["name"] if body.name is None else body.name
        model_path = d["model_path"] if body.model_path is None else body.model_path
        gpu_id = d.get("gpu_id", "") if body.gpu_id is None else (body.gpu_id or "")
        conn.execute(
            "UPDATE services SET name=?, model_path=?, args=?, gpu_id=?, updated_at=? WHERE id=?",
            (name, model_path, json.dumps(args), gpu_id, now(), sid),
        )
    return {"ok": True}


@router.post("/{sid}/start")
def start_service(sid: int):
    """加载模型到 router"""
    model_name = _resolve_model_name(sid)
    try:
        result = router_client.load_model_sync(model_name)
        with get_conn() as conn:
            conn.execute("UPDATE services SET status='loaded', updated_at=? WHERE name=?", (now(), model_name))
        return {"ok": True, "status": "loaded", "detail": result}
    except RuntimeError as e:
        with get_conn() as conn:
            conn.execute("UPDATE services SET status='error', updated_at=? WHERE name=?", (now(), model_name))
        raise HTTPException(400, str(e))


@router.post("/{sid}/stop")
def stop_service(sid: int):
    """从 router 卸载模型（支持 DB id 或按名称）"""
    model_name = _resolve_model_name(sid)
    try:
        result = router_client.unload_model_sync(model_name)
        with get_conn() as conn:
            conn.execute("UPDATE services SET status='unloaded', updated_at=? WHERE name=?", (now(), model_name))
        return {"ok": True, "status": "unloaded", "detail": result}
    except RuntimeError as e:
        raise HTTPException(400, str(e))


def _resolve_model_name(sid) -> str:
    """将 DB id 解析为模型名；若 DB 无记录则尝试直接按名称（router 发现模式）"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if row:
        return dict(row)["name"]
    # DB 无记录：sid 可能是模型名（字符串）？不，路由是 int。尝试用 router 发现列表匹配序号
    raise HTTPException(404, "模型不存在，请先刷新模型列表")


@router.post("/{sid}/restart")
def restart_service(sid: int):
    """重启模型：先卸载再加载（尽力而为）"""
    model_name = _resolve_model_name(sid)
    # 先尝试卸载（失败不阻断，继续加载）
    try:
        router_client.unload_model_sync(model_name)
        import time
        time.sleep(2)  # 等待 router 完成卸载清理
    except RuntimeError:
        pass  # 卸载失败不阻断重启流程
    # 重新加载
    try:
        result = router_client.load_model_sync(model_name)
        with get_conn() as conn:
            conn.execute("UPDATE services SET status='loaded', updated_at=? WHERE name=?", (now(), model_name))
        return {"ok": True, "status": "loaded", "detail": result}
    except RuntimeError as e:
        with get_conn() as conn:
            conn.execute("UPDATE services SET status='error', updated_at=? WHERE name=?", (now(), model_name))
        raise HTTPException(400, str(e))


@router.delete("/{sid}")
def delete_service(sid: int):
    """删除模型注册记录（不删文件）"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
        if not row:
            raise HTTPException(404, "模型不存在")
        conn.execute("DELETE FROM services WHERE id=?", (sid,))
    return {"ok": True}


@router.get("/{sid}/logs")
def service_logs(sid: int, tail: int = 200, since: Optional[str] = None, until: Optional[str] = None):
    """获取 llama-server 运行日志（读取 router.log 文件）"""
    from pathlib import Path
    from datetime import datetime, timezone
    import re

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "模型不存在")
    d = dict(row)

    log_file = Path(settings.data_dir) / "router.log"
    if not log_file.exists():
        return {"logs": "（日志文件不存在，模型未启动或未产生日志）", "total": 0, "file": str(log_file)}

    try:
        raw_lines = log_file.read_text(errors="replace").splitlines()
    except Exception as e:
        return {"logs": f"读取日志失败: {e}", "total": 0, "file": str(log_file)}

    total = len(raw_lines)

    # 时间过滤（如果提供了 since/until）
    # llama.cpp 日志格式示例: "[  1] 12.345.678 I srv ..."  （相对时间，难以解析）
    # 或带绝对时间的行（如果有）。策略：尝试匹配 ISO 时间戳前缀，否则按 mtime 粗略判断
    filtered = raw_lines

    if since or until:
        since_dt = None
        until_dt = None
        if since:
            try:
                since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            except ValueError:
                pass
        if until:
            try:
                until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
            except ValueError:
                pass

        # 尝试按行内时间戳过滤（匹配 HH:MM:SS 或 ISO 格式）
        ts_pattern = re.compile(r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})')
        time_only_pattern = re.compile(r'(\d{2}:\d{2}:\d{2})')

        if since_dt or until_dt:
            # 统一 since/until 时区为 UTC（naive -> UTC，避免与 aware 行时间比较崩溃）
            if since_dt and since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
            if until_dt and until_dt.tzinfo is None:
                until_dt = until_dt.replace(tzinfo=timezone.utc)

            timed_lines = []
            file_mtime = datetime.fromtimestamp(log_file.stat().st_mtime, tz=timezone.utc)
            for line in raw_lines:
                m = ts_pattern.search(line)
                if m:
                    try:
                        line_dt = datetime.fromisoformat(m.group(1))
                    except ValueError:
                        timed_lines.append(line)
                        continue
                else:
                    m2 = time_only_pattern.search(line)
                    if m2:
                        # 只有时间没有日期，用文件 mtime 的日期
                        try:
                            line_dt = datetime.fromisoformat(
                                file_mtime.strftime("%Y-%m-%d") + "T" + m2.group(1)
                            )
                        except ValueError:
                            timed_lines.append(line)
                            continue
                    else:
                        # 无时间戳行：保留（通常是多行日志的续行）
                        timed_lines.append(line)
                        continue

                # 统一时区比较
                if line_dt.tzinfo is None:
                    line_dt = line_dt.replace(tzinfo=timezone.utc)

                if since_dt and line_dt < since_dt:
                    continue
                if until_dt and line_dt > until_dt:
                    continue
                timed_lines.append(line)
            filtered = timed_lines

    # tail 截取
    if tail and tail > 0:
        result_lines = filtered[-tail:]
    else:
        result_lines = filtered

    logs_text = "\n".join(result_lines) if result_lines else "（无匹配日志）"

    return {
        "logs": logs_text,
        "total": len(result_lines),
        "file": str(log_file),
    }


@router.get("/params/schema")
def param_schema():
    """返回参数白名单（前端表单渲染用）"""
    from app.docker_mgr import PARAM_MAP, DEFAULT_ARGS
    return {
        "map": {k: {"flag": v[0], "type": (v[1].__name__ if hasattr(v[1], "__name__") else str(v[1]))} for k, v in PARAM_MAP.items()},
        "defaults": DEFAULT_ARGS,
    }


# ---------- 聊天代理（通过 /v1 统一网关，自动选模型） ----------

class ChatRequest(BaseModel):
    messages: list[dict]
    max_tokens: int = 1024
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    stream: bool = False
    chat_template_kwargs: Optional[dict] = None
    extra: Optional[dict] = None


def _normalize_messages(messages: list) -> list:
    """规范化 messages：末尾必须是 user（剔除末尾空 assistant/连续 assistant）
    防双请求/异常调用导致 llama.cpp 400 "Cannot have 2 or more assistant messages at the end"
    """
    msgs = [m for m in (messages or []) if isinstance(m, dict) and m.get("role")]
    while msgs:
        last = msgs[-1]
        if last.get("role") == "assistant" and not str(last.get("content", "") or "").strip():
            msgs.pop()  # 末尾空 assistant 剔除
        else:
            break
    # 合并末尾连续 assistant（保留最后一条非空）
    cleaned = []
    for m in msgs:
        if cleaned and m.get("role") == "assistant" and cleaned[-1].get("role") == "assistant":
            cleaned[-1] = m  # 保留后一条
        else:
            cleaned.append(m)
    return cleaned


@router.post("/{sid}/chat")
async def chat_proxy(sid: int, body: ChatRequest):
    """转发到 router 的 OpenAI 兼容端点（支持流式）"""
    import httpx
    from fastapi.responses import StreamingResponse

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "模型不存在")
    d = dict(row)
    model_name = d["name"]

    url = f"{settings.router_url}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}

    payload = {
        "model": model_name,
        "messages": _normalize_messages(body.messages),
        "max_tokens": body.max_tokens,
        "stream": body.stream,
    }
    if body.temperature is not None:
        payload["temperature"] = body.temperature
    if body.top_p is not None:
        payload["top_p"] = body.top_p
    if body.chat_template_kwargs is not None:
        payload["chat_template_kwargs"] = body.chat_template_kwargs
    if body.extra:
        payload.update(body.extra)

    timeout = httpx.Timeout(600.0, connect=10.0)

    if not body.stream:
        import time as _time
        t0 = _time.time()
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                r = await client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as e:
                raise HTTPException(502, f"转发失败: {e}")
            if r.status_code != 200:
                raise HTTPException(r.status_code, f"上游返回 {r.status_code}: {r.text[:500]}")
            data = r.json()
            # 埋点统计
            elapsed_ms = int((_time.time() - t0) * 1000)
            usage = data.get("usage", {})
            _record_stats(model_name,
                          prompt_tokens=usage.get("prompt_tokens", 0),
                          completion_tokens=usage.get("completion_tokens", 0),
                          prefill_ms=elapsed_ms)
            return data

    async def gen():
        import time as _time
        t0 = _time.time()
        first_token_time = None
        prompt_tokens = 0
        completion_tokens = 0
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                async with client.stream("POST", url, json=payload, headers=headers) as r:
                    async for line in r.aiter_lines():
                        if line:
                            # 捕获首 token 时间
                            if first_token_time is None and line.startswith("data:") and "[DONE]" not in line:
                                first_token_time = _time.time()
                            # 解析 usage（流式最后 chunk 可能有；llama.cpp 用 timings 字段）
                            if line.startswith("data:") and "[DONE]" not in line:
                                try:
                                    chunk = json.loads(line[5:].strip())
                                    u = chunk.get("usage")
                                    if u:
                                        prompt_tokens = u.get("prompt_tokens", 0)
                                        completion_tokens = u.get("completion_tokens", 0)
                                    else:
                                        t = chunk.get("timings")
                                        if t:
                                            prompt_tokens = t.get("prompt_n", 0)
                                            completion_tokens = t.get("predicted_n", 0)
                                except Exception:
                                    pass
                            yield line + "\n"
            except httpx.HTTPError as e:
                yield f"data: {{\"error\": \"{e}\"}}\n\n"
        # 流式结束后埋点
        total_ms = int((_time.time() - t0) * 1000)
        prefill_ms = int((first_token_time - t0) * 1000) if first_token_time else total_ms
        decode_ms = max(0, total_ms - prefill_ms)
        _record_stats(model_name, prompt_tokens=prompt_tokens,
                      completion_tokens=completion_tokens,
                      prefill_ms=prefill_ms, decode_ms=decode_ms)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------- 客户端配置导出 ----------

@router.get("/{sid}/client-config")
def client_config(sid: int):
    """生成 curl / openclaw / python 三种客户端配置片段"""
    import socket

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "模型不存在")
    d = dict(row)
    model_name = d["name"]

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        host_ip = s.getsockname()[0]
        s.close()
    except Exception:
        host_ip = "<HOST-IP>"

    base = f"http://{host_ip}:{settings.webui_port}/v1"
    # 从 api_keys 表取第一个启用的 key 作为示例
    with get_conn() as conn:
        key_row = conn.execute(
            "SELECT key FROM api_keys WHERE enabled=1 ORDER BY id LIMIT 1"
        ).fetchone()
    key = key_row["key"] if key_row else "<在系统设置生成 API 密钥>"
    auth = f'"Authorization: Bearer {key}"' if key_row else ""
    auth_line = f'  -H {auth} \\' if auth else ""

    curl = f'''curl {base}/chat/completions \\
  -H "Content-Type: application/json" \\
{auth_line}
  -d '{{"model": "{model_name}", "messages": [{{"role": "user", "content": "你好"}}], "max_tokens": 100}}'
'''

    openclaw = f'''# openclaw.json models.providers 片段
"llm-studio": {{
  "type": "openai",
  "baseUrl": "{base}",
  "apiKey": "{key}",
  "models": ["{model_name}"]
}}'''

    python = f'''import openai

client = openai.OpenAI(
    base_url="{base}",
    api_key="{key}",
)
resp = client.chat.completions.create(
    model="{model_name}",
    messages=[{{"role": "user", "content": "你好"}}],
)
print(resp.choices[0].message.content)'''

    return {
        "base_url": base,
        "model": model_name,
        "curl": curl.strip(),
        "openclaw": openclaw.strip(),
        "python": python.strip(),
    }


# ---------- 聊天历史 ----------

class HistoryItem(BaseModel):
    role: str
    content: str = ""
    thinking: str = ""
    session_id: int = 0


class SessionCreate(BaseModel):
    title: str = ""


class SessionRename(BaseModel):
    title: str


@router.get("/{sid}/sessions")
def list_sessions(sid: int):
    """列出服务的所有会话（含消息数、更新时间）"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM chat_history h WHERE h.session_id = s.id AND h.sid = s.sid) as msg_count "
            "FROM chat_sessions s WHERE s.sid=? ORDER BY s.updated_at DESC",
            (sid,),
        ).fetchall()
        # 也加入默认会话(session_id=0)的消息数
        default_count = conn.execute(
            "SELECT COUNT(*) as c FROM chat_history WHERE sid=? AND (session_id=0 OR session_id IS NULL)", (sid,)
        ).fetchone()["c"]
    result = [dict(r) for r in rows]
    # 默认会话始终存在
    result.append({"id": 0, "sid": sid, "title": "默认会话", "created_at": 0, "updated_at": 0, "msg_count": default_count})
    return result


@router.post("/{sid}/sessions")
def create_session(sid: int, body: SessionCreate):
    """新建会话"""
    title = body.title or f"新会话"
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO chat_sessions (sid, title, created_at, updated_at) VALUES (?,?,?,?)",
            (sid, title, now(), now()),
        )
        session_id = cur.lastrowid
    return {"id": session_id, "sid": sid, "title": title}


@router.patch("/{sid}/sessions/{session_id}")
def rename_session(sid: int, session_id: int, body: SessionRename):
    """重命名会话"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE chat_sessions SET title=?, updated_at=? WHERE id=? AND sid=?",
            (body.title, now(), session_id, sid),
        )
    return {"ok": True}


@router.delete("/{sid}/sessions/{session_id}")
def delete_session(sid: int, session_id: int):
    """删除会话（连带历史）"""
    with get_conn() as conn:
        conn.execute("DELETE FROM chat_history WHERE sid=? AND session_id=?", (sid, session_id))
        conn.execute("DELETE FROM chat_sessions WHERE id=? AND sid=?", (session_id, sid))
    return {"ok": True}


@router.get("/{sid}/history")
def get_history(sid: int, session_id: int = 0):
    """获取聊天历史（按会话隔离）"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, role, content, thinking, created_at FROM chat_history "
            "WHERE sid=? AND (session_id=? OR (session_id IS NULL AND ?=0)) ORDER BY id",
            (sid, session_id, session_id),
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/{sid}/history")
def add_history(sid: int, body: HistoryItem):
    """追加聊天历史"""
    sess = body.session_id or 0
    with get_conn() as conn:
        last = conn.execute(
            "SELECT role, content FROM chat_history WHERE sid=? AND (session_id=? OR (session_id IS NULL AND ?=0)) ORDER BY id DESC LIMIT 1",
            (sid, sess, sess),
        ).fetchone()
        if body.role == 'user' and last and last['role'] == 'user' and last['content'] == body.content:
            return {"ok": True, "skipped": "duplicate"}
        cur = conn.execute(
            "INSERT INTO chat_history (sid, session_id, role, content, thinking, created_at) VALUES (?,?,?,?,?,?)",
            (sid, sess, body.role, body.content, body.thinking, now()),
        )
        hid = cur.lastrowid
        # 更新会话 updated_at
        if sess > 0:
            conn.execute("UPDATE chat_sessions SET updated_at=? WHERE id=? AND sid=?", (now(), sess, sid))
    return {"ok": True, "id": hid}


@router.delete("/{sid}/history")
def clear_history(sid: int, session_id: int = 0):
    """清空指定会话的聊天历史"""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM chat_history WHERE sid=? AND (session_id=? OR (session_id IS NULL AND ?=0))",
            (sid, session_id, session_id),
        )
    return {"ok": True}


@router.delete("/{sid}/history/{history_id}")
def delete_history_item(sid: int, history_id: int):
    """删除单条聊天历史"""
    with get_conn() as conn:
        conn.execute("DELETE FROM chat_history WHERE id=? AND sid=?", (history_id, sid))
    return {"ok": True}


# ---------- PDF 解析 ----------

@router.post("/{sid}/parse-pdf")
async def parse_pdf(sid: int, file: UploadFile):
    """上传 PDF 文件，返回提取的文本"""
    from fastapi import UploadFile as _UF
    import io

    content = await file.read()
    if not content:
        raise HTTPException(400, "空文件")

    text = ""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=content, filetype="pdf")
        for page in doc:
            text += page.get_text()
        doc.close()
    except ImportError:
        # PyMuPDF 不可用，尝试 pdfplumber
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                for page in pdf.pages:
                    text += page.extract_text() or ""
        except ImportError:
            raise HTTPException(500, "PDF 解析库未安装（需要 PyMuPDF 或 pdfplumber）")

    if not text.strip():
        text = "（PDF 未提取到文本，可能是扫描件）"

    return {"text": text[:8000]}  # 截断防止超长
