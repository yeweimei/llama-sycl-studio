"""引擎版本管理 API - llama.cpp 二进制升级/回滚"""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.database import get_conn, now
from app.config import settings
from app import proxy

router = APIRouter()

# 二进制存放目录（持久化卷内）
BIN_DIR = Path(settings.data_dir) / "bin"
CURRENT_BIN = Path("/app/llama-server")
GITHUB_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases"
# SYCL fp16 Ubuntu x64 asset 命名模式
ASSET_PATTERN = "bin-ubuntu-sycl-fp16-x64.tar.gz"


def _atomic_replace(src: Path, dst: Path):
    """原子替换：先复制到同目录临时文件，再 os.replace 覆盖。
    os.replace 对运行中文件是原子的（inode 替换），旧进程继续跑旧二进制，
    新启动用新二进制。避免 Text file busy (ETXTBSY)。"""
    tmp = dst.parent / f".{dst.name}.tmp.{os.getpid()}"
    shutil.copy2(str(src), str(tmp))
    os.chmod(str(tmp), 0o755)
    os.replace(str(tmp), str(dst))


def _upsert_setting(conn, key: str, value: str):
    """安全的 upsert：先 UPDATE，rowcount=0 再 INSERT（兼容所有表结构）"""
    cur = conn.execute("UPDATE app_settings SET value=? WHERE key=?", (value, key))
    if cur.rowcount == 0:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?,?)", (key, value))


def _get_current_version() -> str:
    """检测当前 llama-server 版本"""
    try:
        r = subprocess.run(
            [str(CURRENT_BIN), "--version"],
            capture_output=True, text=True, timeout=10,
        )
        output = (r.stdout + r.stderr).strip()
        # 版本输出类似: llama-server --version  b387 (sha...)
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("b") or "version" in line.lower():
                # 提取 bXXXX 格式版本号
                import re
                m = re.search(r"b\d+", line)
                if m:
                    return m.group(0)
        return output[:50] if output else "unknown"
    except Exception:
        return "unknown"


def _list_local_versions() -> list[dict]:
    """列出本地已安装的二进制版本"""
    versions = []
    current = _get_current_version()
    versions.append({"version": current, "active": True, "path": str(CURRENT_BIN)})
    if BIN_DIR.exists():
        for f in sorted(BIN_DIR.glob("llama-server-b*")):
            ver = f.name.replace("llama-server-", "")
            versions.append({"version": ver, "active": False, "path": str(f)})
    return versions


def _fetch_releases(per_page: int = 10) -> list[dict]:
    """从 GitHub API 获取可用版本列表"""
    import urllib.request
    url = f"{GITHUB_API}?per_page={per_page}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "llama-studio/1.0",
        "Accept": "application/vnd.github+json",
    })
    # 走代理
    proxy_settings = proxy.get_settings()
    if proxy_settings.get("proxy_enabled") == "1" and proxy_settings.get("proxy_url"):
        handler = urllib.request.ProxyHandler({
            "http": proxy_settings["proxy_url"],
            "https": proxy_settings["proxy_url"],
        })
        opener = urllib.request.build_opener(handler)
        urllib.request.install_opener(opener)

    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())

    result = []
    for rel in data:
        tag = rel["tag_name"]
        # 找 SYCL fp16 asset
        asset_url = None
        asset_size = 0
        for a in rel["assets"]:
            if ASSET_PATTERN in a["name"]:
                asset_url = a["browser_download_url"]
                asset_size = a["size"]
                break
        if asset_url:
            result.append({
                "version": tag,
                "url": asset_url,
                "size": asset_size,
                "size_human": _human_size(asset_size),
                "published_at": rel.get("published_at", ""),
            })
    return result


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


class UpgradeRequest(BaseModel):
    version: str


class RollbackRequest(BaseModel):
    version: str


@router.get("/version")
def engine_version():
    """当前版本 + 安装历史"""
    return {
        "current": _get_current_version(),
        "installed": _list_local_versions(),
    }


@router.get("/upgrades")
def engine_upgrades():
    """远程可用版本列表"""
    try:
        return _fetch_releases()
    except Exception as e:
        raise HTTPException(500, f"获取版本列表失败: {e}")


@router.post("/upgrade")
def engine_upgrade(body: UpgradeRequest):
    """升级二进制：备份 -> 下载 -> 解压 -> 校验 -> 替换"""
    import urllib.request
    import tarfile
    import tempfile

    version = body.version
    if not version.startswith("b"):
        raise HTTPException(400, "版本号格式错误，应为 bXXXXX")

    # 查找下载 URL
    try:
        releases = _fetch_releases()
    except Exception as e:
        raise HTTPException(500, f"获取版本列表失败: {e}")

    target = next((r for r in releases if r["version"] == version), None)
    if not target:
        raise HTTPException(404, f"未找到版本 {version}")

    # 步骤 1: 备份当前二进制
    current_ver = _get_current_version()
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = BIN_DIR / f"llama-server-{current_ver}"
    if not backup_path.exists() and CURRENT_BIN.exists():
        shutil.copy2(str(CURRENT_BIN), str(backup_path))

    # 步骤 2: 下载
    download_url = target["url"]
    tmp_dir = Path(tempfile.mkdtemp())
    archive_path = tmp_dir / f"llama-{version}.tar.gz"

    try:
        proxy_settings = proxy.get_settings()
        req = urllib.request.Request(download_url, headers={"User-Agent": "llama-studio/1.0"})
        if proxy_settings.get("proxy_enabled") == "1" and proxy_settings.get("proxy_url"):
            handler = urllib.request.ProxyHandler({
                "http": proxy_settings["proxy_url"],
                "https": proxy_settings["proxy_url"],
            })
            opener = urllib.request.build_opener(handler)
            urllib.request.install_opener(opener)

        with urllib.request.urlopen(req, timeout=300) as resp:
            with open(archive_path, "wb") as f:
                shutil.copyfileobj(resp, f)

        # 步骤 3: 解压，找到 llama-server 二进制
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(str(tmp_dir))

        # 查找解压后的 llama-server
        new_bin = None
        for candidate in tmp_dir.rglob("llama-server"):
            if candidate.is_file() and os.access(str(candidate), os.X_OK):
                new_bin = candidate
                break
        if not new_bin:
            raise RuntimeError("解压后未找到 llama-server 可执行文件")

        # 步骤 4: 校验大小（>1MB 才合理）
        if new_bin.stat().st_size < 1_000_000:
            raise RuntimeError(f"二进制文件过小: {new_bin.stat().st_size} bytes")

        # 步骤 5: 原子替换（避免 Text file busy）
        _atomic_replace(new_bin, CURRENT_BIN)

        # 记录到 DB
        with get_conn() as conn:
            _upsert_setting(conn, "engine_version", version)
            _upsert_setting(conn, "engine_last_upgrade", str(now()))

        return {"ok": True, "version": version, "previous": current_ver,
                "message": f"已升级到 {version}，需重启容器生效"}

    except Exception as e:
        # 自动回滚
        if backup_path.exists():
            try: _atomic_replace(backup_path, CURRENT_BIN)
            except Exception: pass
        raise HTTPException(500, f"升级失败已回滚: {e}")
    finally:
        shutil.rmtree(str(tmp_dir), ignore_errors=True)


@router.post("/rollback")
def engine_rollback(body: RollbackRequest):
    """回滚到已安装的旧版本"""
    version = body.version
    backup_path = BIN_DIR / f"llama-server-{version}"
    if not backup_path.exists():
        raise HTTPException(404, f"未找到版本 {version} 的备份")

    current_ver = _get_current_version()
    # 备份当前版本（如果尚未备份）
    current_backup = BIN_DIR / f"llama-server-{current_ver}"
    if not current_backup.exists() and CURRENT_BIN.exists():
        shutil.copy2(str(CURRENT_BIN), str(current_backup))

    try:
        _atomic_replace(backup_path, CURRENT_BIN)
        with get_conn() as conn:
            _upsert_setting(conn, "engine_version", version)
        return {"ok": True, "version": version, "previous": current_ver,
                "message": f"已回滚到 {version}，需重启容器生效"}
    except Exception as e:
        # 恢复原版本
        if current_backup.exists():
            try: _atomic_replace(current_backup, CURRENT_BIN)
            except Exception: pass
        raise HTTPException(500, f"回滚失败已恢复: {e}")
