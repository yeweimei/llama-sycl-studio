"""服务管理 API"""
import json
from typing import Optional

from fastapi import APIRouter, HTTPException, WebSocket
from pydantic import BaseModel

from app import docker_mgr
from app.database import get_conn, now

router = APIRouter()


class ServiceCreate(BaseModel):
    name: str
    model_path: str            # 容器内路径 /models/xxx.gguf
    args: dict = {}
    api_key: Optional[str] = None
    port: Optional[int] = None


class ServiceUpdate(BaseModel):
    args: Optional[dict] = None
    api_key: Optional[str] = None
    name: Optional[str] = None
    model_path: Optional[str] = None
    port: Optional[int] = None


@router.get("")
def list_services():
    docker_mgr.sync_status()
    return docker_mgr.list_services()


@router.post("")
def create_service(body: ServiceCreate):
    try:
        svc = docker_mgr.create_service(
            name=body.name,
            model_path=body.model_path,
            args=body.args or docker_mgr.DEFAULT_ARGS,
            api_key=body.api_key,
            port=body.port,
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    return svc


@router.get("/{sid}")
def get_service(sid: int):
    svc = docker_mgr.get_service(sid)
    if not svc:
        raise HTTPException(404, "服务不存在")
    return svc


@router.put("/{sid}")
def update_service(sid: int, body: ServiceUpdate):
    svc = docker_mgr.get_service(sid)
    if not svc:
        raise HTTPException(404, "服务不存在")
    if svc["status"] == "running" and (body.name or body.model_path or body.port):
        raise HTTPException(400, "请先停止服务再修改名称/模型/端口")

    args = svc["args"] if body.args is None else body.args
    api_key = svc["api_key"] if body.api_key is None else body.api_key
    name = svc["name"] if body.name is None else body.name
    model_path = svc["model_path"] if body.model_path is None else body.model_path
    port = svc["port"] if body.port is None else body.port

    # 校验重名和端口冲突
    with get_conn() as conn:
        dup = conn.execute(
            "SELECT id FROM services WHERE (name=? OR port=?) AND id!=?", (name, port, sid)
        ).fetchone()
    if dup:
        raise HTTPException(400, "服务名或端口已被占用")

    with get_conn() as conn:
        conn.execute(
            "UPDATE services SET name=?, model_path=?, port=?, args=?, api_key=?, updated_at=? WHERE id=?",
            (name, model_path, port, json.dumps(args), api_key, now(), sid),
        )
    return docker_mgr.get_service(sid)


@router.post("/{sid}/clone")
def clone_service(sid: int, name: Optional[str] = None):
    """克隆服务：复制配置为新服务（新端口，不启动）"""
    svc = docker_mgr.get_service(sid)
    if not svc:
        raise HTTPException(404, "服务不存在")
    new_name = name or f"{svc['name']}-copy"
    # 检查重名
    with get_conn() as conn:
        dup = conn.execute("SELECT id FROM services WHERE name=?", (new_name,)).fetchone()
    if dup:
        raise HTTPException(400, f"服务名 {new_name} 已存在")
    try:
        return docker_mgr.create_service(
            name=new_name,
            model_path=svc["model_path"],
            args=svc["args"],
            api_key=svc.get("api_key"),
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@router.post("/{sid}/start")
def start_service(sid: int):
    try:
        return docker_mgr.start_service(sid)
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@router.post("/{sid}/stop")
def stop_service(sid: int):
    return docker_mgr.stop_service(sid)


@router.post("/{sid}/restart")
def restart_service(sid: int):
    try:
        return docker_mgr.restart_service(sid)
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@router.delete("/{sid}")
def delete_service(sid: int):
    docker_mgr.delete_service(sid)
    return {"ok": True}


@router.get("/{sid}/logs")
def service_logs(sid: int, tail: int = 200):
    return {"logs": docker_mgr.get_container_logs(sid, tail)}


@router.get("/params/schema")
def param_schema():
    """返回参数白名单（前端表单渲染用）"""
    return {
        "map": {k: {"flag": v[0], "type": (v[1].__name__ if hasattr(v[1], "__name__") else str(v[1]))} for k, v in docker_mgr.PARAM_MAP.items()},
        "defaults": docker_mgr.DEFAULT_ARGS,
    }


# ---------- 聊天代理（OpenAI 兼容转发，自动带 API Key） ----------

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
    """转发到服务的 OpenAI 兼容端点（支持流式）"""
    import httpx

    svc = docker_mgr.get_service(sid)
    if not svc:
        raise HTTPException(404, "服务不存在")
    if svc["status"] != "running":
        raise HTTPException(400, "服务未运行")

    url = f"http://127.0.0.1:{svc['port']}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    if svc.get("api_key"):
        headers["Authorization"] = f"Bearer {svc['api_key']}"

    payload = {
        "model": "local",
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

    # 流式：SSE 透传
    from fastapi.responses import StreamingResponse

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

    svc = docker_mgr.get_service(sid)
    if not svc:
        raise HTTPException(404, "服务不存在")

    # 宿主机 IP（NUC12 局域网 IP）
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        host_ip = s.getsockname()[0]
        s.close()
    except Exception:
        host_ip = "<NUC12-IP>"

    base = f"http://{host_ip}:{svc['port']}/v1"
    model = svc["model_path"].split("/")[-1].replace(".gguf", "")
    key = svc.get("api_key") or "<API_KEY>"
    auth = f'"Authorization: Bearer {key}"' if svc.get("api_key") else ""

    curl = f'''curl {base}/chat/completions \\
  -H "Content-Type: application/json" \\
  {"  -H " + auth + " \\" if auth else ""}
  -d '{{"model": "{model}", "messages": [{{"role": "user", "content": "你好"}}], "max_tokens": 100}}'
'''

    openclaw = f'''# openclaw.json models.providers 片段
"llm-nuc12": {{
  "type": "openai",
  "baseUrl": "{base}",
  "apiKey": "{key}",
  "models": ["{model}"]
}}'''

    python = f'''import openai

client = openai.OpenAI(
    base_url="{base}",
    api_key="{key}",
)
resp = client.chat.completions.create(
    model="{model}",
    messages=[{{"role": "user", "content": "你好"}}],
)
print(resp.choices[0].message.content)'''

    return {
        "base_url": base,
        "model": model,
        "curl": curl.strip(),
        "openclaw": openclaw.strip(),
        "python": python.strip(),
    }


# ---------- 日志 WebSocket 实时流 ----------

@router.websocket("/{sid}/logs/ws")
async def logs_ws(websocket: WebSocket, sid: int):
    """实时推送容器日志（docker logs -f）"""
    import asyncio
    import docker

    await websocket.accept()
    svc = docker_mgr.get_service(sid)
    if not svc:
        await websocket.send_json({"type": "error", "message": "服务不存在"})
        await websocket.close()
        return

    try:
        client = docker.from_env()
        container = client.containers.get(f"llm-{svc['name']}")
    except Exception as e:
        await websocket.send_json({"type": "error", "message": f"容器不可用: {e}"})
        await websocket.close()
        return

    async def pump():
        """在后台线程读 docker 日志流，通过队列转发"""
        queue = asyncio.Queue()
        stop = asyncio.Event()

        def reader():
            try:
                for line in container.logs(stream=True, follow=True, tail=100):
                    if stop.is_set():
                        break
                    queue.put_nowait(line.decode("utf-8", errors="replace"))
            except Exception as e:
                queue.put_nowait(f"[日志流结束] {e}\n")

        t = asyncio.get_event_loop().run_in_executor(None, reader)
        try:
            while True:
                line = await queue.get()
                await websocket.send_json({"type": "log", "line": line})
        finally:
            stop.set()

    try:
        await pump()
    except Exception:
        pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
