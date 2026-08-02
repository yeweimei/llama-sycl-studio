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


@router.post("/{sid}/start")
def start_service(sid: int):
    """加载模型到 router（支持 DB id 或按名称）"""
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
def service_logs(sid: int, tail: int = 200):
    """获取 router 日志（简化版：返回 router 状态摘要）"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "模型不存在")
    d = dict(row)
    healthy = router_client.health_check_sync()
    loaded = router_client.get_loaded_models_sync() if healthy else []
    # 找到当前模型的信息
    model_info = ""
    if isinstance(loaded, list):
        for m in loaded:
            if m.get("model", m.get("id", "")) == d["name"]:
                model_info = json.dumps(m, indent=2, ensure_ascii=False)
                break
    logs = f"=== Router Health: {'OK' if healthy else 'UNREACHABLE'} ===\n"
    logs += f"=== Router URL: {settings.router_url} ===\n"
    logs += f"=== Model: {d['name']} ===\n"
    if model_info:
        logs += f"--- Loaded Info ---\n{model_info}\n"
    else:
        logs += "(模型未加载或无信息)\n"
    return {"logs": logs}


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
