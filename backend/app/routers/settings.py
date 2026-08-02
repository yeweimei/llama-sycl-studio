"""设置 API - API Key 管理、参数模板、镜像管理"""
import json
import secrets

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.database import get_conn, now
from app import proxy

router = APIRouter()


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
        rows = conn.execute("SELECT id, name, enabled, created_at FROM api_keys ORDER BY id").fetchall()
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


# ---------- 镜像管理 ----------

@router.get("/images")
def list_images():
    import docker
    try:
        client = docker.from_env()
        imgs = client.images.list()
        out = []
        for im in imgs:
            for tag in im.tags:
                out.append({
                    "tag": tag,
                    "size": im.attrs.get("Size", 0),
                    "created": im.attrs.get("Created", ""),
                })
        return out
    except Exception as e:
        raise HTTPException(500, f"无法连接 Docker: {e}")


@router.get("/image-versions")
def image_versions():
    """llama.cpp 官方 SYCL 镜像可用版本（从 ghcr API）"""
    import urllib.request
    import json

    try:
        req = urllib.request.Request(
            "https://ghcr.io/v2/ggml-org/llama.cpp/tags/list?n=200",
            headers={"User-Agent": "llama-studio/0.1"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        tags = [t for t in data.get("tags", []) if "intel" in t.lower() and t[0].isdigit()]
        tags.sort(key=lambda x: int(x.split("-b")[-1]) if "-b" in x else 0, reverse=True)
        return {"current": settings.llama_image, "available": tags[:20]}
    except Exception as e:
        return {"current": settings.llama_image, "available": [], "error": str(e)}


@router.post("/images/pull")
def pull_image(tag: str):
    """拉取指定版本的 llama.cpp SYCL 镜像（后台）"""
    import threading

    def _pull(t):
        import docker
        try:
            client = docker.from_env()
            client.images.pull(t)
        except Exception as e:
            print(f"pull {t} failed: {e}")

    full = f"ghcr.io/ggml-org/llama.cpp:{tag}" if ":" not in tag else tag
    threading.Thread(target=_pull, args=(full,), daemon=True).start()
    return {"ok": True, "pulling": full}
