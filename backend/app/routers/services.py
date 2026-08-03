"""服务管理 API - router 模型池管理（替代旧容器管理）"""
import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import router_client
from app.database import get_conn, now
from app.config import settings

router = APIRouter()


class ServiceCreate(BaseModel):
    name: str
    model_path: str
    args: dict = {}
    api_key: Optional[str] = None


class ServiceUpdate(BaseModel):
    args: Optional[dict] = None
    api_key: Optional[str] = None
    name: Optional[str] = None
    model_path: Optional[str] = None


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
                    "INSERT INTO services (name, model_path, args, api_key, status, created_at, updated_at) "
                    "VALUES (?,?,?,?, 'unloaded', ?, ?)",
                    (mid, f"/models/{mid}.gguf", "{}", None, now(), now()),
                )
                db_models[mid] = {
                    "id": cur.lastrowid, "name": mid, "model_path": f"/models/{mid}.gguf",
                    "args": {}, "api_key": None, "status": "unloaded",
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
        result.append({
            "id": db_info.get("id", 0),
            "name": mid,
            "model_path": db_info.get("model_path", f"/models/{mid}.gguf"),
            "args": db_info.get("args", {}),
            "api_key": db_info.get("api_key"),
            "status": state,
            "loaded": is_loaded,
            "loaded_info": loaded_detail,
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
                "api_key": db_info.get("api_key"),
                "status": "unavailable",
                "loaded": False,
                "loaded_info": {},
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
            "INSERT INTO services (name, model_path, args, api_key, status, created_at, updated_at) "
            "VALUES (?,?,?,?, 'unloaded', ?, ?)",
            (body.name, body.model_path, json.dumps(body.args or {}), body.api_key, now(), now()),
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
        api_key = d["api_key"] if body.api_key is None else body.api_key
        name = d["name"] if body.name is None else body.name
        model_path = d["model_path"] if body.model_path is None else body.model_path
        conn.execute(
            "UPDATE services SET name=?, model_path=?, args=?, api_key=?, updated_at=? WHERE id=?",
            (name, model_path, json.dumps(args), api_key, now(), sid),
        )
    return {"ok": True}


def _get_service_row(sid) -> dict:
    """从 DB 获取服务行（含解析后的 args）"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "模型不存在，请先刷新模型列表")
    d = dict(row)
    d["args"] = json.loads(d["args"] or "{}")
    return d


@router.post("/{sid}/start")
def start_service(sid: int):
    """加载模型到 router（携带 DB 中配置的推理参数）"""
    svc = _get_service_row(sid)
    model_name = svc["name"]
    args = svc.get("args", {})
    try:
        result = router_client.load_model_sync(model_name, params=args if args else None)
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
    """重启模型：先卸载再加载（携带 DB 中配置的推理参数）"""
    svc = _get_service_row(sid)
    model_name = svc["name"]
    args = svc.get("args", {})
    # 先尝试卸载（失败不阻断，继续加载）
    try:
        router_client.unload_model_sync(model_name)
        import time
        time.sleep(2)  # 等待 router 完成卸载清理
    except RuntimeError:
        pass  # 卸载失败不阻断重启流程
    # 重新加载（带参数）
    try:
        result = router_client.load_model_sync(model_name, params=args if args else None)
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
        "messages": body.messages,
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
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                r = await client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as e:
                raise HTTPException(502, f"转发失败: {e}")
            if r.status_code != 200:
                raise HTTPException(r.status_code, f"上游返回 {r.status_code}: {r.text[:500]}")
            return r.json()

    async def gen():
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                async with client.stream("POST", url, json=payload, headers=headers) as r:
                    async for line in r.aiter_lines():
                        if line:
                            yield line + "\n"
            except httpx.HTTPError as e:
                yield f"data: {{\"error\": \"{e}\"}}\n\n"

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
    key = d.get("api_key") or "<API_KEY>"
    auth = f'"Authorization: Bearer {key}"' if d.get("api_key") else ""

    curl = f'''curl {base}/chat/completions \\
  -H "Content-Type: application/json" \\
  {"  -H " + auth + " \\" if auth else ""}
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
