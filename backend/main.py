# llama-sycl-studio 后端
"""
LLM 推理服务管理台 - FastAPI 后端入口
单容器一体化架构：WebUI + llama-server router 同容器运行
"""
import os
import sys
from pathlib import Path

# 确保能 import app 包
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app import auth as auth_mod
from app import idle_unload, self_heal
from app.routers import services, models, downloads, gpu, settings as settings_router
from app.routers import auth as auth_router
from app.routers import presets, tags, stats, engine

app = FastAPI(
    title="LLM Studio",
    description="llama.cpp SYCL 推理服务管理台（单容器一体化）",
    version="1.0.0",
)

# CORS：开发时前端 vite 不同端口
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 初始化数据库
init_db()

# 启动空闲自动卸载后台任务
idle_unload.start_idle_unload_loop()
# 启动自愈监控后台任务（M3：实例异常自动重启）
self_heal.start_self_heal_loop()
# 启动僵尸收割线程（防 llama-server 僵尸占端口导致脏实例复用）
from app import instance_mgr as _im
_im.start_zombie_harvester()

# ---------- 认证中间件 ----------
# 不需要认证的路径
_PUBLIC_API_PATHS = {"/api/auth/login", "/api/auth/status", "/api/auth/setup", "/api/health"}
# /v1/* 中不需要认证的路径
_PUBLIC_V1_PATHS = {"/v1/models"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """拦截 /api/* 和 /v1/* 请求，校验 Bearer token"""
    path = request.url.path

    # /api/* 路径
    if path.startswith("/api/"):
        if path in _PUBLIC_API_PATHS:
            return await call_next(request)
        auth_header = request.headers.get("Authorization", "")
        token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
        if not auth_mod.check_token(token):
            return JSONResponse(status_code=401, content={"detail": "未认证"})
        return await call_next(request)

    # /v1/* 路径 - 需要认证（除了 /v1/models）
    if path.startswith("/v1/"):
        if path in _PUBLIC_V1_PATHS:
            return await call_next(request)
        auth_header = request.headers.get("Authorization", "")
        token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
        # 接受 WebUI 登录 token 或 api_keys 表中的有效 key
        if not auth_mod.check_token(token) and not auth_mod.check_api_key(token):
            return JSONResponse(status_code=401, content={"detail": "未认证"})
        return await call_next(request)

    return await call_next(request)


# 路由注册
app.include_router(auth_router.router, prefix="/api/auth", tags=["auth"])
app.include_router(services.router, prefix="/api/services", tags=["services"])
app.include_router(models.router, prefix="/api/models", tags=["models"])
app.include_router(downloads.router, prefix="/api/downloads", tags=["downloads"])
app.include_router(gpu.router, prefix="/api/gpu", tags=["gpu"])
app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"])
app.include_router(presets.router, prefix="/api/presets", tags=["presets"])
app.include_router(tags.router, prefix="/api/model-tags", tags=["tags"])
app.include_router(stats.router, prefix="/api/stats", tags=["stats"])
app.include_router(engine.router, prefix="/api/engine", tags=["engine"])


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


# ---------- /v1/* 反向代理到内部 llama-server router ----------
# 支持 SSE 流式转发

_V1_PROXY_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}


def _sanitize_tool_patterns_schema(node) -> None:
    """递归清洗 JSON schema：剔除未锚定（非 ^...$）的 pattern 字段。

    llama.cpp json-schema-to-grammar 要求 pattern 必须以 ^ 开头、$ 结尾，
    否则返回 400 "Pattern must start with '^' and end with '$'"。
    仅存在于 tool 调用的 parameters schema 中，剔除不改变结构语义。
    """
    if isinstance(node, dict):
        if "pattern" in node:
            p = node["pattern"]
            if not (isinstance(p, str) and p.startswith("^") and p.endswith("$")):
                del node["pattern"]
        for v in node.values():
            _sanitize_tool_patterns_schema(v)
    elif isinstance(node, list):
        for it in node:
            _sanitize_tool_patterns_schema(it)


def _sanitize_tools(payload: dict) -> bool:
    """就地清洗 payload 中的 tools schema（若存在）。返回是否发生了修改。"""
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return False
    changed = False
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") if isinstance(t.get("function"), dict) else None
        params = (fn or {}).get("parameters") if fn else None
        if isinstance(params, dict):
            before = json.dumps(params)
            _sanitize_tool_patterns_schema(params)
            if json.dumps(params) != before:
                changed = True
    return changed


def _resolve_instance_base(model_name: str) -> str | None:
    """按模型名解析实例 base url（per-model 架构）"""
    try:
        from app.database import get_conn
        from app import instance_mgr
        with get_conn() as conn:
            row = conn.execute(
                "SELECT id FROM services WHERE name=?", (model_name,)
            ).fetchone()
        if not row:
            # 尝试用 router_id 匹配
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT id FROM services WHERE model_path LIKE ?", (f"%/{model_name}.gguf",)
                ).fetchone()
        if not row:
            return None
        sid = row["id"]
        st = instance_mgr.instance_status(sid)
        if st.get("running"):
            return instance_mgr.url_for(sid)
        return None
    except Exception:
        return None


@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def v1_proxy(path: str, request: Request):
    """反向代理 /v1/* 到对应模型实例（per-model 架构，支持 SSE 流式）

    根据请求体 model 字段路由到该模型的独立 llama-server 实例；
    无法解析模型（如 /v1/models）时回退到 router（若仍存在）。
    """
    import httpx
    import json as _json

    # /v1/models：返回已注册服务列表（OpenAI 兼容格式），不再依赖 router
    if path == "models":
        try:
            import time as _t
            from app.routers.services import list_services
            svcs = list_services()
            created = int(_t.time())
            data = []
            for s in svcs:
                data.append({
                    "id": s.get("name"),
                    "object": "model",
                    "owned_by": "llama-studio",
                    "created": created,
                })
            return {"object": "list", "data": data}
        except Exception:
            pass

    # 读取请求体（先解析 model 字段用于路由）
    body = await request.body()
    model_name = ""
    if body:
        try:
            _payload = _json.loads(body)
            model_name = str(_payload.get("model") or _payload.get("model_id") or "")
        except Exception:
            pass

    # 路由到模型实例
    target_base = None
    if model_name:
        target_base = _resolve_instance_base(model_name)
    if not target_base:
        # 回退：旧 router（兼容）
        target_base = settings.router_url
    target_url = f"{target_base}/v1/{path}"

    # 记录模型调用时间（空闲自动卸载用）
    if model_name:
        idle_unload.touch_model_usage(model_name)
    elif body:
        try:
            _payload = _json.loads(body)
            _m2 = _payload.get("model") or _payload.get("model_id") or ""
            if _m2:
                idle_unload.touch_model_usage(str(_m2))
        except Exception:
            pass

    # 转发请求头
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)

    # 读取请求体
    body = await request.body()

    # 记录模型调用时间（空闲自动卸载用）
    if body:
        try:
            import json as _json
            _payload = _json.loads(body)
            _model = _payload.get("model") or _payload.get("model_id") or ""
            if _model:
                idle_unload.touch_model_usage(str(_model))
        except Exception:
            pass

    # 判断是否是流式请求 + 清洗工具 schema（llama.cpp 要求 pattern 锚定）
    is_stream = False
    chat_log_id = 0
    chat_user_msg = ""
    if request.method == "POST" and body:
        try:
            import json
            payload = json.loads(body)
            is_stream = payload.get("stream", False)
            if path in ("chat/completions", "chat", "completions"):
                if _sanitize_tools(payload):
                    body = json.dumps(payload).encode("utf-8")
                # 对话内容日志：创建 running 记录（仅 chat 请求）
                try:
                    msgs = payload.get("messages") or []
                    chat_user_msg = services._last_user_message(msgs)
                    chat_log_id = services._chat_log_create(model_name, 1 if is_stream else 0, chat_user_msg)
                except Exception:
                    chat_log_id = 0
        except Exception:
            pass

    timeout = httpx.Timeout(600.0, connect=10.0)

    if is_stream:
        # SSE 流式转发
        async def gen():
            import json as _json2
            _resp_buf = []
            _think_buf = []
            _line_buf = ""
            import time as _t
            _last_flush = _t.time()

            def _flush():
                nonlocal _last_flush
                if _resp_buf or _think_buf:
                    services._chat_log_append(chat_log_id, "".join(_resp_buf), "".join(_think_buf))
                    _resp_buf.clear()
                    _think_buf.clear()
                _last_flush = _t.time()

            async with httpx.AsyncClient(timeout=timeout) as client:
                try:
                    async with client.stream("POST", target_url, content=body, headers=headers) as r:
                        async for chunk in r.aiter_bytes():
                            if chat_log_id and chunk:
                                _line_buf += chunk.decode("utf-8", "ignore")
                                while "\n" in _line_buf:
                                    line, _line_buf = _line_buf.split("\n", 1)
                                    line = line.strip()
                                    if line.startswith("data:") and "[DONE]" not in line:
                                        try:
                                            _c = _json2.loads(line[5:].strip())
                                            delta = (_c.get("choices") or [{}])[0].get("delta", {})
                                            cc = delta.get("content")
                                            tt = delta.get("reasoning_content")
                                            if isinstance(cc, str) and cc:
                                                _resp_buf.append(cc)
                                            if isinstance(tt, str) and tt:
                                                _think_buf.append(tt)
                                        except Exception:
                                            pass
                                # 定期落库
                                if _resp_buf or _think_buf:
                                    if _t.time() - _last_flush >= 1.5 or len("".join(_resp_buf)) >= 4096:
                                        _flush()
                            yield chunk
                except httpx.HTTPError as e:
                    if chat_log_id:
                        services._chat_log_finish(chat_log_id, ok=False, status_code=502, error=str(e))
                    yield f"data: {{\"error\": \"{e}\"}}\n\n"
            if chat_log_id:
                _flush()
                services._chat_log_finish(chat_log_id, ok=True, status_code=200)

        return StreamingResponse(gen(), media_type="text/event-stream")

    # 非流式：普通转发
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            r = await client.request(
                request.method,
                target_url,
                content=body,
                headers=headers,
                params=request.query_params,
            )
        except httpx.HTTPError as e:
            if chat_log_id:
                services._chat_log_finish(chat_log_id, ok=False, status_code=502, error=str(e))
            raise HTTPException(502, f"Router 不可达: {e}")

    # 非流式：记录对话内容
    if chat_log_id and path in ("chat/completions", "chat", "completions") and request.method == "POST":
        try:
            import time as _t
            # 上游非 200：记录失败
            if r.status_code != 200:
                err_text = ""
                try:
                    _err_json = r.json()
                    err_text = str(_err_json.get("error", _err_json))[:500]
                except Exception:
                    err_text = (r.text or "")[:500]
                services._chat_log_finish(chat_log_id, ok=False, status_code=r.status_code,
                                          error=err_text, total_ms=0)
            else:
                _resp_json = r.json()
                choice = (_resp_json.get("choices") or [{}])[0]
                msg = choice.get("message", {})
                resp_text = msg.get("content", "") or ""
                think_text = msg.get("reasoning_content", "") or ""
                usage = _resp_json.get("usage", {})
                services._chat_log_append(chat_log_id, resp_text[:20000], think_text[:20000])
                services._chat_log_finish(chat_log_id, ok=True, status_code=r.status_code,
                                          prompt_tokens=usage.get("prompt_tokens", 0),
                                          completion_tokens=usage.get("completion_tokens", 0),
                                          total_ms=0)
        except Exception:
            services._chat_log_finish(chat_log_id, ok=True, status_code=r.status_code)

        # 透传响应
        resp_headers = {}
        for k, v in r.headers.items():
            if k.lower() not in ("transfer-encoding", "content-encoding", "content-length"):
                resp_headers[k] = v

        return JSONResponse(
            content=r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text,
            status_code=r.status_code,
            headers=resp_headers,
        )


# 前端构建产物（生产模式挂载，SPA history fallback）
_frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=str(_frontend_dist / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        """SPA history 路由 fallback：非 API/v1 路径都返回 index.html"""
        from fastapi.responses import FileResponse
        if full_path.startswith("api/") or full_path.startswith("v1/"):
            raise HTTPException(404)
        f = _frontend_dist / "index.html"
        if f.exists():
            return FileResponse(str(f))
        return {"detail": "前端未构建"}
