"""身份验证 API - 登录/设置密码/退出/状态"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app import auth as auth_mod

router = APIRouter()


class PasswordBody(BaseModel):
    password: str


@router.get("/status")
def auth_status(request: Request):
    """检查认证状态：是否已设置密码 + 当前请求是否已认证"""
    configured = auth_mod.is_password_configured()
    token = _extract_token(request)
    authenticated = auth_mod.check_token(token) if configured else False
    return {"configured": configured, "authenticated": authenticated}


@router.post("/setup")
def setup_password(body: PasswordBody):
    """首次设置管理员密码"""
    if auth_mod.is_password_configured():
        raise HTTPException(400, "管理员密码已设置，不可重复设置")
    if len(body.password) < 4:
        raise HTTPException(400, "密码至少 4 位")
    auth_mod.set_password(body.password)
    token = auth_mod.create_token()
    return {"ok": True, "token": token}


@router.post("/login")
def login(body: PasswordBody):
    """登录"""
    if not auth_mod.is_password_configured():
        raise HTTPException(400, "未设置管理员密码，请先初始化")
    stored = _get_stored_hash()
    if not auth_mod.verify_password(body.password, stored):
        raise HTTPException(401, "密码错误")
    token = auth_mod.create_token()
    return {"token": token}


@router.post("/logout")
def logout(request: Request):
    """退出登录"""
    token = _extract_token(request)
    if token:
        auth_mod.revoke_token(token)
    return {"ok": True}


def _extract_token(request: Request) -> str:
    """从 Authorization 头提取 Bearer token"""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return ""


def _get_stored_hash() -> str:
    from app.database import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key='admin_password'"
        ).fetchone()
    return row["value"] if row else ""
