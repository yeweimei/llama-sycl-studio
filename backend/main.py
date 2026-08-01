# llama-sycl-studio 后端
"""
LLM 推理服务管理台 - FastAPI 后端入口
部署在 NUC12，管理本地 llama.cpp SYCL Docker 容器
"""
import os
import sys
from pathlib import Path

# 确保能 import app 包
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routers import services, models, downloads, gpu, settings as settings_router

app = FastAPI(
    title="LLM 推理服务管理台",
    description="管理 NUC12 上的 llama.cpp SYCL Docker 推理服务",
    version="0.1.0",
)

# CORS：开发时前端 vite 不同端口
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: 生产环境收紧
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由注册
app.include_router(services.router, prefix="/api/services", tags=["services"])
app.include_router(models.router, prefix="/api/models", tags=["models"])
app.include_router(downloads.router, prefix="/api/downloads", tags=["downloads"])
app.include_router(gpu.router, prefix="/api/gpu", tags=["gpu"])
app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"])


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.1.0"}


# 前端构建产物（生产模式挂载）
_frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
