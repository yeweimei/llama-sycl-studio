"""模型下载 API - 对接 HuggingFace / ModelScope"""
import os
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.database import get_conn, now
from app import proxy

router = APIRouter()

# 下载任务注册表（内存态，进度实时更新）
_tasks: dict[int, dict] = {}
_task_lock = threading.Lock()


class DownloadRequest(BaseModel):
    source: str = "huggingface"          # huggingface / modelscope
    repo_id: str
    filename: Optional[str] = None       # 指定文件；None = 列出可选
    mirror: Optional[str] = None         # hf-mirror.com 等


@router.get("/sources")
def list_sources():
    return [
        {"id": "huggingface", "name": "HuggingFace", "default_url": "https://huggingface.co"},
        {"id": "modelscope", "name": "ModelScope", "default_url": "https://modelscope.cn"},
    ]


@router.post("/search")
def search_models(body: DownloadRequest):
    """搜索模型仓库（HF / ModelScope）"""
    import urllib.request
    import json
    from urllib.parse import quote

    query = body.repo_id.strip()
    if not query:
        return []

    try:
        if body.source == "modelscope":
            return _search_modelscope(query)
        return _search_huggingface(query)
    except Exception as e:
        raise HTTPException(400, f"搜索失败: {e}")


def _search_huggingface(query: str) -> list[dict]:
    import urllib.request
    import json
    from urllib.parse import quote

    base = proxy.get_hf_base()
    # HF 搜索 API：按关键词 + GGUF 过滤
    url = (
        f"{base}/api/models?search={quote(query)}"
        f"&filter=gguf&sort=downloads&direction=-1&limit=12"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "llama-studio/0.1"})
    with proxy.build_opener().open(req, timeout=25) as resp:
        data = json.loads(resp.read())

    results = []
    for m in data:
        rid = m.get("id", "")
        # 搜索接口不返回 siblings 详情，filter=gguf 已过滤，直接采用
        results.append({
            "repo_id": rid,
            "name": rid.split("/")[-1],
            "author": rid.split("/")[0] if "/" in rid else "",
            "description": (m.get("description") or "")[:200],
            "downloads": m.get("downloads", 0),
            "likes": m.get("likes", 0),
            "tags": m.get("tags", [])[:6],
            "modified": (m.get("lastModified") or "")[:10],
        })
    return results


def _search_modelscope(query: str) -> list[dict]:
    import urllib.request
    import json
    from urllib.parse import quote

    url = f"https://modelscope.cn/api/v1/dolphin/models?PageSize=12&PageNumber=1&SortBy=Default&Target=&SingleCriterion={quote(query)}"
    req = urllib.request.Request(url, headers={"User-Agent": "llama-studio/0.1"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())

    results = []
    for m in data.get("Data", {}).get("Model", {}).get("Models", []) or []:
        results.append({
            "repo_id": m.get("Path", ""),
            "name": m.get("Name", ""),
            "author": m.get("Owner", {}).get("Name", "") if isinstance(m.get("Owner"), dict) else "",
            "description": (m.get("Description") or "")[:200],
            "downloads": m.get("Downloads", 0),
            "likes": m.get("Likes", 0),
            "tags": m.get("Tags", [])[:6],
            "modified": "",
        })
    return [r for r in results if r["repo_id"]]


@router.post("/list-files")
def list_repo_files(body: DownloadRequest):
    """列出仓库内可下载文件（用于选择量化版本）"""
    try:
        if body.source == "modelscope":
            return _list_modelscope(body.repo_id)
        return _list_huggingface(body.repo_id)
    except Exception as e:
        raise HTTPException(400, f"获取文件列表失败: {e}")


def _list_huggingface(repo_id: str) -> list[dict]:
    import urllib.request
    import json

    base = proxy.get_hf_base()
    # 通过 HF API 获取文件列表
    url = f"{base}/api/models/{repo_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "llama-studio/0.1"})
    with proxy.build_opener().open(req, timeout=25) as resp:
        data = json.loads(resp.read())

    files = []
    for s in data.get("siblings", []):
        fn = s.get("rfilename", "")
        if fn.endswith(".gguf"):
            size = s.get("size")  # HF API 的 siblings 通常无 size，走 HEAD
            files.append({"filename": fn, "size": size})

    # 补大小：HEAD 请求（最多 8 个文件，避免慢）
    import urllib.error
    for f in files[:8]:
        try:
            hurl = f"{base}/{repo_id}/resolve/main/{f['filename']}"
            hreq = urllib.request.Request(hurl, method="HEAD", headers={"User-Agent": "llama-studio/0.1"})
            with proxy.build_opener().open(hreq, timeout=12) as hresp:
                cl = hresp.headers.get("Content-Length")
                if cl:
                    f["size"] = int(cl)
        except Exception:
            pass
    return files


def _list_modelscope(repo_id: str) -> list[dict]:
    import urllib.request
    import json

    url = f"https://modelscope.cn/api/v1/models/{repo_id}/repo/files"
    req = urllib.request.Request(url, headers={"User-Agent": "llama-studio/0.1"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())

    files = []
    for item in data.get("Data", {}).get("Files", []):
        fn = item.get("Path", "")
        if fn.endswith(".gguf"):
            files.append({"filename": fn, "size": item.get("Size")})
    return files


@router.post("")
def start_download(body: DownloadRequest):
    """启动下载任务"""
    if not body.filename:
        raise HTTPException(400, "请先选择要下载的文件")

    # 目标路径：model_dir/<repo 最后一段>/<filename>
    repo_short = body.repo_id.split("/")[-1]
    target_dir = Path(settings.model_dir) / repo_short
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / Path(body.filename).name

    # 写 DB（先拿到自增 id，作为内存 task 的 key）
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO download_tasks (source, repo_id, filename, local_path, status, created_at, updated_at) "
            "VALUES (?,?,?,?, 'downloading', ?, ?)",
            (body.source, body.repo_id, body.filename, str(target), now(), now()),
        )
        tid = cur.lastrowid

    # 内存任务用 DB id 作 key（保证 progress 接口能查到）
    with _task_lock:
        _tasks[tid] = {
            "id": tid,
            "source": body.source,
            "repo_id": body.repo_id,
            "filename": body.filename,
            "local_path": str(target),
            "status": "downloading",
            "progress": 0.0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "error": None,
            "created_at": now(),
        }

    # 后台线程下载
    t = threading.Thread(target=_download_worker, args=(tid, body), daemon=True)
    t.start()
    return _tasks[tid]


def _download_worker(tid: int, body: DownloadRequest):
    """后台下载线程（支持断点续传）"""
    import urllib.request

    task = _tasks[tid]
    target = Path(task["local_path"])
    tmp = target.with_suffix(target.suffix + ".part")

    # 构造 URL
    if body.source == "modelscope":
        base = body.mirror or "https://modelscope.cn"
        url = f"{base}/models/{body.repo_id}/resolve/master/{body.filename}"
    else:
        base = body.mirror or proxy.get_hf_base()
        url = f"{base}/{body.repo_id}/resolve/main/{body.filename}"

    try:
        # 已下载部分（断点续传）
        existing = tmp.stat().st_size if tmp.exists() else 0
        headers = {"User-Agent": "llama-studio/0.1"}
        if existing:
            headers["Range"] = f"bytes={existing}-"

        req = urllib.request.Request(url, headers=headers)
        opener = proxy.build_opener()
        with opener.open(req, timeout=60) as resp, open(tmp, "ab") as f:
            total = existing + int(resp.headers.get("Content-Length", 0) or 0)
            task["total_bytes"] = total
            task["downloaded_bytes"] = existing
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
                task["downloaded_bytes"] += len(chunk)
                if total > 0:
                    task["progress"] = round(task["downloaded_bytes"] / total * 100, 1)

        # 完成：改名为正式文件
        tmp.rename(target)
        task["status"] = "done"
        task["progress"] = 100.0
        with get_conn() as conn:
            conn.execute(
                "UPDATE download_tasks SET status='done', progress=100, downloaded_bytes=?, total_bytes=?, updated_at=? WHERE id=?",
                (task["downloaded_bytes"], task["total_bytes"], now(), tid),
            )
    except Exception as e:
        task["status"] = "error"
        task["error"] = str(e)
        with get_conn() as conn:
            conn.execute(
                "UPDATE download_tasks SET status='error', updated_at=? WHERE id=?",
                (now(), tid),
            )


@router.get("/tasks")
def list_tasks():
    """列出所有下载任务（含历史）"""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM download_tasks ORDER BY id DESC LIMIT 50").fetchall()
    return [dict(r) for r in rows]


@router.get("/tasks/{tid}/progress")
def task_progress(tid: int):
    """实时进度：优先内存态（下载线程），回退 DB（进程重启后的历史任务）"""
    if tid in _tasks:
        return _tasks[tid]
    # 内存没有：查 DB
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM download_tasks WHERE id=?", (tid,)).fetchone()
    if not row:
        raise HTTPException(404, "任务不存在")
    task = dict(row)
    # DB 标记 downloading 但内存无线程 = 进程重启导致下载中断
    if task["status"] == "downloading":
        task["status"] = "error"
        task["error"] = "下载进程中断（服务重启），请重新发起下载"
        with get_conn() as conn:
            conn.execute(
                "UPDATE download_tasks SET status='error', updated_at=? WHERE id=?",
                (now(), tid),
            )
    return task


@router.delete("/tasks/{tid}")
def cancel_task(tid: int):
    """取消任务（删除临时文件）"""
    if tid in _tasks:
        task = _tasks.pop(tid)
        tmp = Path(task["local_path"]).with_suffix(Path(task["local_path"]).suffix + ".part")
        if tmp.exists():
            tmp.unlink()
        return {"ok": True}
    raise HTTPException(404, "任务不存在")
