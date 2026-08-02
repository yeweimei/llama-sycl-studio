"""Router Client - 封装对容器内 llama-server router 的 HTTP 调用"""
import httpx
from typing import Optional

from app.config import settings


def _base() -> str:
    return settings.router_url.rstrip("/")


async def list_models() -> list[dict]:
    """GET /v1/models - 获取模型列表（含已加载和未加载的）"""
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f"{_base()}/v1/models")
        if r.status_code != 200:
            return []
        data = r.json()
    out = []
    for m in data.get("data", []):
        out.append({
            "id": m.get("id", ""),
            "object": m.get("object", "model"),
            "owned_by": m.get("owned_by", "router"),
            "loaded": m.get("loaded", False),
        })
    return out


async def get_loaded_models() -> list[dict]:
    """GET /models - 获取当前驻留状态+启动参数"""
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f"{_base()}/models")
        if r.status_code != 200:
            return []
        return r.json()


async def load_model(model_id: str) -> dict:
    """POST /models/load - 加载模型到 router"""
    async with httpx.AsyncClient(timeout=300.0) as c:
        r = await c.post(f"{_base()}/models/load", json={"model": model_id})
        if r.status_code != 200:
            raise RuntimeError(f"加载失败 ({r.status_code}): {r.text[:500]}")
        return r.json()


async def unload_model(model_id: str) -> dict:
    """POST /models/unload - 从 router 卸载模型"""
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(f"{_base()}/models/unload", json={"model": model_id})
        if r.status_code != 200:
            raise RuntimeError(f"卸载失败 ({r.status_code}): {r.text[:500]}")
        return r.json()


async def health_check() -> bool:
    """GET /health - 检查 router 是否在线"""
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{_base()}/health")
            return r.status_code == 200
    except Exception:
        return False


def list_models_sync() -> list[dict]:
    """同步版本 - 获取模型列表"""
    with httpx.Client(timeout=10.0) as c:
        r = c.get(f"{_base()}/v1/models")
        if r.status_code != 200:
            return []
        data = r.json()
    out = []
    for m in data.get("data", []):
        out.append({
            "id": m.get("id", ""),
            "object": m.get("object", "model"),
            "owned_by": m.get("owned_by", "router"),
            "loaded": m.get("loaded", False),
        })
    return out


def get_loaded_models_sync() -> list[dict]:
    """同步版本 - 获取驻留状态"""
    with httpx.Client(timeout=10.0) as c:
        r = c.get(f"{_base()}/models")
        if r.status_code != 200:
            return []
        return r.json()


def load_model_sync(model_id: str) -> dict:
    """同步版本 - 加载模型"""
    with httpx.Client(timeout=300.0) as c:
        r = c.post(f"{_base()}/models/load", json={"model": model_id})
        if r.status_code != 200:
            raise RuntimeError(f"加载失败 ({r.status_code}): {r.text[:500]}")
        return r.json()


def unload_model_sync(model_id: str) -> dict:
    """同步版本 - 卸载模型"""
    with httpx.Client(timeout=30.0) as c:
        r = c.post(f"{_base()}/models/unload", json={"model": model_id})
        if r.status_code != 200:
            raise RuntimeError(f"卸载失败 ({r.status_code}): {r.text[:500]}")
        return r.json()


def health_check_sync() -> bool:
    """同步版本 - 健康检查"""
    try:
        with httpx.Client(timeout=5.0) as c:
            r = c.get(f"{_base()}/health")
            return r.status_code == 200
    except Exception:
        return False
