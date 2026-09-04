# llama-sycl-studio 后端
"""
LLM 推理服务管理台 - FastAPI 后端入口
单容器一体化架构：WebUI + llama-server router 同容器运行
"""
import os
import sys
from pathlib import Path

import httpx

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
from app.routers import presets, tags, stats, engine, perf

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
app.include_router(perf.router, prefix="/api/perf", tags=["perf"])


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


# ---------- OpenAI 兼容统一接入端点 ----------
# 按 OpenAI 最新格式规范化 /v1/* 反代：按请求 model 字段路由到 per-model 实例，
# 标准错误 envelope，接入并发闸/预热/degraded/draining 稳定性保护，共享连接池。
# 不再回退到已废弃的中心 router（per-model 模式只直接命中各实例）。
_V1_SHARED_CLIENT: httpx.AsyncClient | None = None


def _get_v1_shared_client() -> httpx.AsyncClient:
    global _V1_SHARED_CLIENT
    if _V1_SHARED_CLIENT is None:
        _V1_SHARED_CLIENT = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=128, max_keepalive_connections=32),
            timeout=httpx.Timeout(600.0, connect=10.0),
        )
    return _V1_SHARED_CLIENT


def _openai_error(status: int, message: str, err_type: str = "invalid_request_error",
                  param: str | None = None, code: str | None = None):
    """标准 OpenAI 错误 envelope：{"error": {message, type, param, code}}"""
    err = {"message": message, "type": err_type}
    if param is not None:
        err["param"] = param
    if code is not None:
        err["code"] = code
    return JSONResponse(status_code=status, content={"error": err})


def _is_chat_path(path: str) -> bool:
    return path in ("chat/completions", "chat", "completions")


def _resolve_target(model_name: str) -> dict | None:
    """按模型名解析目标实例 → {sid, base, state}；未知模型返回 None"""
    from app.database import get_conn
    from app import instance_mgr
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM services WHERE name=?", (model_name,)).fetchone()
    if not row:
        with get_conn() as conn:
            row = conn.execute("SELECT id FROM services WHERE model_path LIKE ?", (f"%/{model_name}.gguf",)).fetchone()
    if not row:
        return None
    sid = row["id"]
    st = instance_mgr.instance_status(sid)
    return {"sid": sid, "base": instance_mgr.url_for(sid), "state": st.get("state") or "stopped"}


@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def v1_proxy(path: str, request: Request):
    """OpenAI 兼容统一接入端点（per-model 架构）。

    - /v1/models                   → 已注册模型列表（OpenAI list 格式）
    - /v1/chat/completions          → 按 model 路由到实例，支持 SSE 流式 + 工具调用
    - /v1/embeddings /v1/completions → 同样按 model 路由透传到实例
    统一：标准 OpenAI 错误 envelope；chat 路径接入并发闸/预热/degraded/draining；
    模型未知(404 model_not_found)、未启动(400 model_not_loaded) 均返回标准错误，
    不再回退到废弃中心 router。
    """
    from app import instance_mgr, idle_unload
    import json as _json

    # /v1/models：无需 model 路由
    if path == "models":
        try:
            from app.routers.services import list_services
            import time as _t
            created = int(_t.time())
            data = [{"id": s.get("name"), "object": "model",
                     "owned_by": "llama-studio", "created": created}
                    for s in list_services()]
            try:
                stats._record_stats("v1/models", stream=False, ok=True, status_code=200,
                                    total_ms=0, endpoint="/v1/models", method=request.method)
            except Exception:
                pass
            return {"object": "list", "data": data}
        except Exception:
            pass

    # 解析 body 拿 model
    body = await request.body()
    model_name, payload = "", None
    if body:
        try:
            payload = _json.loads(body)
            model_name = str(payload.get("model") or payload.get("model_id") or "")
        except Exception:
            pass

    is_chat = _is_chat_path(path) and request.method == "POST"

    # 路由 + 标准错误
    target = _resolve_target(model_name) if model_name else None
    if model_name and target is None:
        return _openai_error(404, f"模型 '{model_name}' 不存在", "model_not_found", code="model_not_found")
    if not target:
        return _openai_error(400, "请求缺少可识别的 model 字段", "invalid_request_error", code="missing_model")

    sid, target_base = target["sid"], target["base"]

    # ---- 稳定性保护（对齐前端 chat_proxy）----
    st = instance_mgr.instance_status(sid)
    state = st.get("state")
    if state == "degraded":
        return _openai_error(503, f"模型 '{model_name}' 实例无响应（健康检查失败），请重启模型",
                             "service_unavailable", code="service_unavailable")
    if state != "running":
        return _openai_error(400, f"模型 '{model_name}' 未启动（状态 {state or 'stopped'}），请先启动模型",
                             "model_not_loaded", code="model_not_loaded")
    if instance_mgr.is_draining(sid):
        return _openai_error(503, f"模型 '{model_name}' 正在停止（draining），请稍后重试",
                             "service_unavailable", code="service_unavailable")
    if is_chat and instance_mgr.is_warming(sid):
        return _openai_error(503, f"模型 '{model_name}' 正在预热（编译内核），请稍后重试",
                             "service_unavailable", code="service_unavailable")

    # 并发闸（chat 生成路径）+ draining 在途计数
    slot_held = False
    if is_chat:
        if not await instance_mgr.acquire_slot(sid, model_name):
            return _openai_error(503, f"模型 '{model_name}' 并发已满（>{instance_mgr.slot_limit(model_name)} 路进行中），请稍后重试",
                                 "service_unavailable", code="service_unavailable")
        slot_held = True
    request_started = False
    if not instance_mgr.begin_request(sid):
        if slot_held:
            instance_mgr.release_slot(sid)
        return _openai_error(503, f"模型 '{model_name}' 正在停止（draining），请稍后重试",
                             "service_unavailable", code="service_unavailable")
    request_started = True

    idle_unload.touch_model_usage(model_name)

    def _cleanup():
        if request_started:
            instance_mgr.end_request(sid)
        if slot_held:
            instance_mgr.release_slot(sid)

    # 转发头（去 host/content-length）
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)

    # 工具 schema 清洗（llama.cpp pattern 需 ^...$ 锚定）+ 对话内容日志
    chat_log_id = 0
    if is_chat and payload is not None:
        if _sanitize_tools(payload):
            body = _json.dumps(payload).encode("utf-8")
        try:
            msgs = payload.get("messages") or []
            chat_user_msg = services._last_user_message(msgs)
            chat_log_id = services._chat_log_create(model_name, 1 if payload.get("stream") else 0, chat_user_msg)
        except Exception:
            chat_log_id = 0

    is_stream = bool(payload and payload.get("stream"))
    target_url = f"{target_base}/v1/{path}"
    timeout = httpx.Timeout(600.0, connect=10.0)
    import time as _tstat
    _t_start = _tstat.time()

    if is_stream:
        # SSE 流式转发（忠实透传 llama.cpp，含 data: [DONE] 终结）
        async def gen():
            import json as _json2
            import time as _t
            _resp_buf, _think_buf, _line_buf = [], [], ""
            _last_flush = _t.time()
            _up_status = 0

            def _flush():
                nonlocal _last_flush
                if _resp_buf or _think_buf:
                    services._chat_log_append(chat_log_id, "".join(_resp_buf), "".join(_think_buf))
                    _resp_buf.clear()
                    _think_buf.clear()
                _last_flush = _t.time()

            try:
                async with _get_v1_shared_client().stream("POST", target_url, content=body, headers=headers, timeout=timeout) as r:
                    _up_status = r.status_code
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
                            if _resp_buf or _think_buf:
                                if _t.time() - _last_flush >= 1.5 or len("".join(_resp_buf)) >= 4096:
                                    _flush()
                        yield chunk
            except httpx.HTTPError as e:
                if chat_log_id:
                    services._chat_log_finish(chat_log_id, ok=False, status_code=502, error=str(e))
                try:
                    stats._record_stats(model_name or f"/v1/{path}", stream=True, ok=False,
                                        status_code=502, total_ms=int((_tstat.time() - _t_start) * 1000),
                                        error=str(e), endpoint=f"/v1/{path}", method=request.method)
                except Exception:
                    pass
                yield f"data: {{\"error\": \"{e}\"}}\n\n"
            finally:
                if chat_log_id:
                    try:
                        _flush()
                        services._chat_log_finish(chat_log_id, ok=True, status_code=_up_status or 200)
                    except Exception:
                        pass
                try:
                    stats._record_stats(model_name or f"/v1/{path}", stream=True,
                                        ok=200 <= (_up_status or 200) < 400, status_code=_up_status or 200,
                                        total_ms=int((_tstat.time() - _t_start) * 1000),
                                        error="" if (_up_status or 200) < 400 else "stream error",
                                        endpoint=f"/v1/{path}", method=request.method)
                except Exception:
                    pass
                _cleanup()

        return StreamingResponse(gen(), media_type="text/event-stream")

    # 非流式：普通转发（共享 client）
    try:
        r = await _get_v1_shared_client().request(
            request.method, target_url, content=body, headers=headers,
            params=request.query_params, timeout=timeout,
        )
    except httpx.HTTPError as e:
        if chat_log_id:
            services._chat_log_finish(chat_log_id, ok=False, status_code=502, error=str(e))
        try:
            stats._record_stats(model_name or f"/v1/{path}", stream=False, ok=False,
                                status_code=502, total_ms=int((_tstat.time() - _t_start) * 1000),
                                error=str(e), endpoint=f"/v1/{path}", method=request.method)
        except Exception:
            pass
        return _openai_error(502, f"上游不可达: {e}", "upstream_error", code="upstream_error")
    finally:
        _cleanup()

    # 对话内容记录（非流式 chat）
    if chat_log_id and request.method == "POST":
        try:
            if r.status_code != 200:
                err_text = ""
                try:
                    _err_json = r.json()
                    err_text = str(_err_json.get("error", _err_json))[:500]
                except Exception:
                    err_text = (r.text or "")[:500]
                services._chat_log_finish(chat_log_id, ok=False, status_code=r.status_code, error=err_text, total_ms=0)
            else:
                _resp_json = r.json()
                choice = (_resp_json.get("choices") or [{}])[0]
                msg = choice.get("message", {})
                usage = _resp_json.get("usage", {})
                services._chat_log_append(chat_log_id,
                                          (msg.get("content") or "")[:20000],
                                          (msg.get("reasoning_content") or "")[:20000])
                services._chat_log_finish(chat_log_id, ok=True, status_code=r.status_code,
                                          prompt_tokens=usage.get("prompt_tokens", 0),
                                          completion_tokens=usage.get("completion_tokens", 0),
                                          total_ms=0)
        except Exception:
            try:
                services._chat_log_finish(chat_log_id, ok=True, status_code=r.status_code)
            except Exception:
                pass

    # 统计埋点（端点维度，含 embeddings 等全部 /v1/*）
    try:
        _stat_usage = {}
        try:
            _stat_data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if isinstance(_stat_data, dict):
                _stat_usage = _stat_data.get("usage", {}) or {}
        except Exception:
            pass
        stats._record_stats(
            model_name or f"/v1/{path}",
            prompt_tokens=_stat_usage.get("prompt_tokens", 0),
            completion_tokens=_stat_usage.get("completion_tokens", 0),
            stream=False, ok=r.status_code < 400, status_code=r.status_code,
            total_ms=int((_tstat.time() - _t_start) * 1000),
            error="" if r.status_code < 400 else (r.text or "")[:300],
            endpoint=f"/v1/{path}", method=request.method,
        )
    except Exception:
        pass

    # 透传响应
    resp_headers = {k: v for k, v in r.headers.items()
                    if k.lower() not in ("transfer-encoding", "content-encoding", "content-length")}
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
