"""网络代理工具 - 供模型搜索/下载使用（避免被墙）"""
from app.database import get_conn

DEFAULT_KEYS = {
    "proxy_enabled": "0",          # 是否启用代理
    "proxy_url": "",               # 如 http://192.168.3.232:7897
    "hf_mirror": "",               # 可选 HF 镜像，如 https://hf-mirror.com
}


def get_settings() -> dict:
    """读取代理设置"""
    out = dict(DEFAULT_KEYS)
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    for r in rows:
        out[r["key"]] = r["value"]
    return out


def save_settings(settings: dict):
    """保存代理设置（只更新已知 key）"""
    with get_conn() as conn:
        for k, v in settings.items():
            if k in DEFAULT_KEYS:
                conn.execute(
                    "INSERT INTO app_settings (key, value) VALUES (?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (k, str(v)),
                )


def get_proxy_handler() -> dict:
    """返回 urllib 用的代理 handler（未启用则 None）"""
    s = get_settings()
    if s.get("proxy_enabled") != "1" or not s.get("proxy_url"):
        return None
    return {"http": s["proxy_url"], "https": s["proxy_url"]}


def get_hf_base() -> str:
    """HF 基础地址（支持镜像）"""
    s = get_settings()
    return (s.get("hf_mirror") or "https://huggingface.co").rstrip("/")


def build_opener():
    """构建带代理的 urllib opener"""
    import urllib.request
    proxy = get_proxy_handler()
    if proxy:
        handler = urllib.request.ProxyHandler(proxy)
        return urllib.request.build_opener(handler)
    return urllib.request.build_opener()
