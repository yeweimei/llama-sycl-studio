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
    include_mmproj: bool = True          # 是否联动下载 mmproj 投影文件
    mmproj_filename: Optional[str] = None  # 手动指定 mmproj 版本（如 mmproj-Q8_0.gguf）；空=自动选第一个


@router.get("/sources")
def list_sources():
    return [
        {"id": "huggingface", "name": "HuggingFace", "default_url": "https://huggingface.co"},
        {"id": "modelscope", "name": "ModelScope", "default_url": "https://modelscope.cn"},
    ]


@router.post("/search")
def search_models(body: DownloadRequest):
    """搜索模型仓库（HF / ModelScope）"""
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

    url = f"https://modelscope.cn/openapi/v1/models?search={quote(query)}&page_number=1&page_size=12"
    req = urllib.request.Request(url, headers={"User-Agent": "llama-studio/0.1"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())

    results = []
    for m in data.get("data", {}).get("models", []) or []:
        repo_id = m.get("id", "")
        results.append({
            "repo_id": repo_id,
            "name": m.get("display_name", repo_id),
            "author": repo_id.split("/")[0] if "/" in repo_id else "",
            "description": (m.get("description") or "")[:200],
            "downloads": m.get("downloads", 0),
            "likes": m.get("likes", 0),
            "tags": m.get("tags", [])[:6],
            "modified": (m.get("last_modified") or "")[:10],
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
    url = f"{base}/api/models/{repo_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "llama-studio/0.1"})
    with proxy.build_opener().open(req, timeout=25) as resp:
        data = json.loads(resp.read())

    files = []
    for s in data.get("siblings", []):
        fn = s.get("rfilename", "")
        if fn.endswith(".gguf"):
            size = s.get("size")
            files.append({
                "filename": fn,
                "size": size,
                "is_mmproj": fn.startswith("mmproj"),
            })

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
            files.append({
                "filename": fn,
                "size": item.get("Size"),
                "is_mmproj": Path(fn).name.startswith("mmproj"),
            })
    return files


# ------------------------------------------------------------------ #
# 下载任务管理
# ------------------------------------------------------------------ #

@router.post("")
def start_download(body: DownloadRequest):
    """启动下载任务；仓库含 mmproj 时自动联动下载（可关闭）"""
    if not body.filename:
        raise HTTPException(400, "请先选择要下载的文件")

    repo_short = body.repo_id.split("/")[-1]
    target_dir = Path(settings.model_dir) / repo_short
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / Path(body.filename).name

    # 写 DB
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO download_tasks (source, repo_id, filename, local_path, status, created_at, updated_at) "
            "VALUES (?,?,?,?, 'downloading', ?, ?)",
            (body.source, body.repo_id, body.filename, str(target), now(), now()),
        )
        tid = cur.lastrowid

    _launch_worker(tid, body.source, body.repo_id, body.filename, str(target), body.mirror)

    # 联动下载 mmproj：若仓库含 mmproj 文件且主文件不是 mmproj 本身
    linked = []
    if body.include_mmproj and not Path(body.filename).name.startswith("mmproj"):
        try:
            if body.source == "modelscope":
                repo_files = _list_modelscope(body.repo_id)
            else:
                repo_files = _list_huggingface(body.repo_id)
            mmproj_files = [f for f in repo_files if f.get("is_mmproj")]
            # 手动指定版本优先；否则自动选第一个
            picked = None
            if body.mmproj_filename:
                wanted = Path(body.mmproj_filename).name
                picked = next((f for f in mmproj_files if Path(f["filename"]).name == wanted), None)
            if picked is None and mmproj_files:
                picked = mmproj_files[0]
            if picked:
                mm_name = Path(picked["filename"]).name
                mm_target = target_dir / mm_name
                if not mm_target.exists():
                    with get_conn() as conn:
                        cur2 = conn.execute(
                            "INSERT INTO download_tasks (source, repo_id, filename, local_path, status, created_at, updated_at) "
                            "VALUES (?,?,?,?, 'downloading', ?, ?)",
                            (body.source, body.repo_id, picked["filename"], str(mm_target), now(), now()),
                        )
                        mm_tid = cur2.lastrowid
                    _launch_worker(mm_tid, body.source, body.repo_id, picked["filename"], str(mm_target), body.mirror)
                    linked.append({"id": mm_tid, "filename": picked["filename"]})
        except Exception:
            pass  # 联动失败不阻断主下载

    resp = _tasks.get(tid, {"id": tid, "status": "error", "error": "启动失败"})
    if linked:
        resp["mmproj_linked"] = linked
        resp["message"] = "检测到多模态投影文件，已联动下载 mmproj"
    return resp


def _launch_worker(tid: int, source: str, repo_id: str, filename: str, local_path: str, mirror: str = None):
    """启动下载线程（供 start_download / retry 复用）"""
    with _task_lock:
        _tasks[tid] = {
            "id": tid,
            "source": source,
            "repo_id": repo_id,
            "filename": filename,
            "local_path": local_path,
            "status": "downloading",
            "progress": 0.0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "error": None,
            "paused": False,
            "stop": False,
            "created_at": now(),
        }

    t = threading.Thread(
        target=_download_worker,
        args=(tid, source, repo_id, filename, local_path, mirror),
        daemon=True,
    )
    t.start()


def _download_worker(tid: int, source: str, repo_id: str, filename: str, local_path: str, mirror: str = None):
    """后台下载线程（支持断点续传 + 暂停/继续）"""
    import urllib.request

    task = _tasks[tid]
    target = Path(local_path)
    tmp = target.with_suffix(target.suffix + ".part")

    # 构造 URL
    if source == "modelscope":
        base = mirror or "https://modelscope.cn"
        url = f"{base}/models/{repo_id}/resolve/master/{filename}"
    else:
        base = mirror or proxy.get_hf_base()
        url = f"{base}/{repo_id}/resolve/main/{filename}"

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
                # 检查停止标志
                if task.get("stop"):
                    task["status"] = "cancelled"
                    return

                # 检查暂停标志
                if task.get("paused"):
                    time.sleep(0.5)
                    continue

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
        if task.get("stop"):
            task["status"] = "cancelled"
            return
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


@router.post("/tasks/{tid}/pause")
def pause_task(tid: int):
    """暂停下载任务"""
    with _task_lock:
        if tid not in _tasks:
            raise HTTPException(404, "任务不在内存中（可能已完成或已中断）")
        task = _tasks[tid]
        if task["status"] != "downloading":
            raise HTTPException(400, f"当前状态 {task['status']} 不可暂停")
        task["paused"] = True
        task["status"] = "paused"
    with get_conn() as conn:
        conn.execute("UPDATE download_tasks SET status='paused', updated_at=? WHERE id=?", (now(), tid))
    return {"ok": True, "status": "paused"}


@router.post("/tasks/{tid}/resume")
def resume_task(tid: int):
    """继续下载任务"""
    with _task_lock:
        if tid not in _tasks:
            raise HTTPException(404, "任务不在内存中（可能已完成或已中断）")
        task = _tasks[tid]
        if task["status"] != "paused":
            raise HTTPException(400, f"当前状态 {task['status']} 不可继续")
        task["paused"] = False
        task["status"] = "downloading"
    with get_conn() as conn:
        conn.execute("UPDATE download_tasks SET status='downloading', updated_at=? WHERE id=?", (now(), tid))
    return {"ok": True, "status": "downloading"}


@router.post("/tasks/{tid}/retry")
def retry_task(tid: int):
    """重试失败的下载任务（清除 .part 重新开始）"""
    with _task_lock:
        if tid not in _tasks:
            # 内存没有，从 DB 查
            with get_conn() as conn:
                row = conn.execute("SELECT * FROM download_tasks WHERE id=?", (tid,)).fetchone()
            if not row:
                raise HTTPException(404, "任务不存在")
            r = dict(row)
            if r["status"] != "error":
                raise HTTPException(400, "只能重试失败状态的任务")
            source = r["source"]
            repo_id = r["repo_id"]
            filename = r["filename"]
            local_path = r["local_path"]
        else:
            task = _tasks[tid]
            if task["status"] != "error":
                raise HTTPException(400, "只能重试失败状态的任务")
            source = task["source"]
            repo_id = task["repo_id"]
            filename = task["filename"]
            local_path = task["local_path"]

    # 删除旧 .part 文件
    target = Path(local_path)
    tmp = target.with_suffix(target.suffix + ".part")
    if tmp.exists():
        tmp.unlink()

    # 更新 DB 状态
    with get_conn() as conn:
        conn.execute(
            "UPDATE download_tasks SET status='downloading', progress=0, downloaded_bytes=0, "
            "total_bytes=0, updated_at=? WHERE id=?",
            (now(), tid),
        )

    # 重新启动下载线程
    _launch_worker(tid, source, repo_id, filename, local_path)
    return {"ok": True, "status": "downloading"}


@router.delete("/tasks/{tid}")
def delete_task(tid: int):
    """删除任务：停止活跃线程 + 删 .part + 删 DB 记录 + 移除内存"""
    with _task_lock:
        task = _tasks.pop(tid, None)

    if task:
        # 停止活跃线程
        task["stop"] = True
        # 删除 .part 临时文件
        target = Path(task["local_path"])
        tmp = target.with_suffix(target.suffix + ".part")
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass

    # 删 DB 记录（无论内存有没有）
    with get_conn() as conn:
        conn.execute("DELETE FROM download_tasks WHERE id=?", (tid,))

    return {"ok": True}
