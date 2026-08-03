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
from app.routers import services, models, downloads, gpu, settings as settings_router
from app.routers import auth as auth_router
from app.routers import presets

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


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


# ---------- /v1/* 反向代理到内部 llama-server router ----------
# 支持 SSE 流式转发

_V1_PROXY_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}


@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def v1_proxy(path: str, request: Request):
    """反向代理 /v1/* 到内部 router（支持 SSE 流式）"""
    import httpx

    target_url = f"{settings.router_url}/v1/{path}"

    # 转发请求头
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)

    # 读取请求体
    body = await request.body()

    # 判断是否是流式请求
    is_stream = False
    if request.method == "POST" and body:
        try:
            import json
            payload = json.loads(body)
            is_stream = payload.get("stream", False)
        except Exception:
            pass

    timeout = httpx.Timeout(600.0, connect=10.0)

    if is_stream:
        # SSE 流式转发
        async def gen():
            async with httpx.AsyncClient(timeout=timeout) as client:
                try:
                    async with client.stream("POST", target_url, content=body, headers=headers) as r:
                        async for chunk in r.aiter_bytes():
                            yield chunk
                except httpx.HTTPError as e:
                    yield f"data: {{\"error\": \"{e}\"}}\n\n"

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
            raise HTTPException(502, f"Router 不可达: {e}")

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
