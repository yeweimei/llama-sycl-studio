"""服务管理 API"""
import json
from typing import Optional

from fastapi import APIRouter, HTTPException
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
    args = svc["args"] if body.args is None else body.args
    api_key = svc["api_key"] if body.api_key is None else body.api_key
    with get_conn() as conn:
        conn.execute(
            "UPDATE services SET args=?, api_key=?, updated_at=? WHERE id=?",
            (json.dumps(args), api_key, now(), sid),
        )
    return docker_mgr.get_service(sid)


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
        "map": {k: {"flag": v[0], "type": v[1].__name__} for k, v in docker_mgr.PARAM_MAP.items()},
        "defaults": docker_mgr.DEFAULT_ARGS,
    }
