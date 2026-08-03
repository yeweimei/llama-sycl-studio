"""身份验证 - 密码哈希 + Token 管理（标准库实现，无第三方依赖）"""
import hashlib
import secrets
import time
from app.database import get_conn

# 加盐
_SALT = "llama-studio-2026"

# 内存 token 存储: {token: expiry_timestamp}
_tokens: dict[str, float] = {}

# Token 有效期：7 天
TOKEN_TTL = 7 * 24 * 3600


def hash_password(password: str) -> str:
    """SHA256 加盐哈希"""
    return hashlib.sha256(f"{_SALT}{password}".encode()).hexdigest()


def verify_password(password: str, stored_hash: str) -> bool:
    """校验密码"""
    return hash_password(password) == stored_hash


def is_password_configured() -> bool:
    """是否已设置管理员密码"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key='admin_password'"
        ).fetchone()
    return row is not None and bool(row["value"])


def set_password(password: str):
    """设置管理员密码（首次设置）"""
    h = hash_password(password)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES ('admin_password', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (h,),
        )


def create_token() -> str:
    """生成登录 token"""
    token = secrets.token_hex(32)
    _tokens[token] = time.time() + TOKEN_TTL
    # 清理过期 token
    _cleanup_tokens()
    return token


def check_token(token: str) -> bool:
    """校验 token 是否有效"""
    if not token:
        return False
    expiry = _tokens.get(token)
    if expiry is None:
        return False
    if time.time() > expiry:
        _tokens.pop(token, None)
        return False
    return True


def revoke_token(token: str):
    """撤销 token（退出登录）"""
    _tokens.pop(token, None)


def _cleanup_tokens():
    """清理过期 token"""
    now = time.time()
    expired = [t for t, exp in _tokens.items() if now > exp]
    for t in expired:
        _tokens.pop(t, None)


def check_api_key(key: str) -> bool:
    """校验 API Key 是否有效（查 api_keys 表）"""
    if not key:
        return False
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM api_keys WHERE key=? AND enabled=1", (key,)
        ).fetchone()
    return row is not None
