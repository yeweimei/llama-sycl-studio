"""设置 API - API Key 管理、参数模板、网络代理"""
import json
import secrets

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.database import get_conn, now
from app import proxy

router = APIRouter()


# ---------- Router 全局上下文 ----------

@router.get("/router-ctx")
def get_router_ctx():
    """读取 router 全局上下文（ROUTER_CTX 环境变量）

    说明：llama.cpp router 模式下，所有子模型 ctx 被全局 -c 钳制，
    模型预设里的 ctx_size 不生效。修改需在部署时设 ROUTER_CTX 并重启容器。
    """
    import os
    val = os.environ.get("ROUTER_CTX", "8192")
    return {"router_ctx": int(val) if str(val).isdigit() else 8192}


# ---------- 网络代理 ----------

class ProxySettings(BaseModel):
    proxy_enabled: bool = False
    proxy_url: str = ""
    hf_mirror: str = ""


@router.get("/proxy")
def get_proxy_settings():
    """读取网络代理设置"""
    s = proxy.get_settings()
    return {
        "proxy_enabled": s.get("proxy_enabled") == "1",
        "proxy_url": s.get("proxy_url", ""),
        "hf_mirror": s.get("hf_mirror", ""),
    }


@router.put("/proxy")
def save_proxy_settings(body: ProxySettings):
    """保存网络代理设置（搜索/下载立即生效）"""
    proxy.save_settings({
        "proxy_enabled": "1" if body.proxy_enabled else "0",
        "proxy_url": body.proxy_url.strip(),
        "hf_mirror": body.hf_mirror.strip(),
    })
    return get_proxy_settings()


# ---------- API Keys ----------

class ApiKeyCreate(BaseModel):
    name: str


@router.get("/api-keys")
def list_api_keys():
    with get_conn() as conn:
        rows = conn.execute("SELECT id, name, key, enabled, created_at FROM api_keys ORDER BY id").fetchall()
    return [dict(r) for r in rows]


@router.post("/api-keys")
def create_api_key(body: ApiKeyCreate):
    key = "sk-llm-" + secrets.token_hex(16)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO api_keys (name, key, enabled, created_at) VALUES (?,?,1,?)",
            (body.name, key, now()),
        )
    return {"name": body.name, "key": key, "created_at": now()}


@router.delete("/api-keys/{kid}")
def delete_api_key(kid: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM api_keys WHERE id=?", (kid,))
    return {"ok": True}


@router.post("/api-keys/{kid}/toggle")
def toggle_api_key(kid: int):
    with get_conn() as conn:
        row = conn.execute("SELECT enabled FROM api_keys WHERE id=?", (kid,)).fetchone()
        if not row:
            raise HTTPException(404, "key 不存在")
        conn.execute("UPDATE api_keys SET enabled=? WHERE id=?", (1 - row["enabled"], kid))
    return {"ok": True}


# ---------- 参数模板 ----------

class TemplateCreate(BaseModel):
    name: str
    args: dict


@router.get("/templates")
def list_templates():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM templates ORDER BY id").fetchall()
    return [{**dict(r), "args": json.loads(r["args"])} for r in rows]


@router.post("/templates")
def create_template(body: TemplateCreate):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO templates (name, args, created_at) VALUES (?,?,?)",
            (body.name, json.dumps(body.args), now()),
        )
    return {"ok": True}


@router.delete("/templates/{tid}")
def delete_template(tid: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM templates WHERE id=?", (tid,))
    return {"ok": True}


# ---------- 容器信息 ----------

@router.get("/container-info")
def container_info():
    """返回当前容器/单容器架构的信息"""
    from app import router_client
    healthy = router_client.health_check_sync()
    return {
        "architecture": "single-container",
        "router_url": settings.router_url,
        "router_healthy": healthy,
        "model_dir": settings.model_dir,
        "webui_port": settings.webui_port,
        "models_max": settings.models_max,
    }


# ---------- 告警配置（M7 飞书通知） ----------

class AlertConfig(BaseModel):
    webhook: str = ""
    enabled: bool = True


@router.get("/alert")
def get_alert_config():
    """读取告警配置"""
    from app import alert
    return alert.get_alert_config()


@router.put("/alert")
def save_alert_config(body: AlertConfig):
    """保存告警配置"""
    from app import alert
    alert.save_alert_config(body.webhook, body.enabled)
    return {"ok": True}


@router.post("/alert/test")
def test_alert():
    """发送测试告警"""
    from app import alert
    ok = alert.send_alert("告警测试", "这是一条测试告警，收到说明配置正确", force=True)
    if not ok:
        from fastapi import HTTPException
        raise HTTPException(400, "发送失败：请检查 webhook 配置")
    return {"ok": True}
