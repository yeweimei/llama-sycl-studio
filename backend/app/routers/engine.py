"""引擎版本管理 API - llama.cpp 二进制升级/回滚"""
import json
import os
import re
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
# 各后端构建的 Ubuntu x64 asset 命名模式（flavor → 资产名子串）
FLAVOR_ASSETS = {
    "sycl-fp16": "bin-ubuntu-sycl-fp16-x64.tar.gz",
    "vulkan": "bin-ubuntu-vulkan-x64.tar.gz",
}
DEFAULT_FLAVOR = "sycl-fp16"


def _flavor_dir(flavor: str, version: str) -> str:
    """flavor 对应的备份目录名：sycl-fp16 保持 b{version}（兼容现有备份目录），
    其余 flavor 用 {flavor}-{version}（如 vulkan-b10622）与 sycl 并存"""
    if flavor == DEFAULT_FLAVOR:
        return version
    return f"{flavor}-{version}"


def _dir_flavor(name: str) -> str:
    """从备份目录名反推 flavor：vulkan-b10622 → vulkan；b10622 → sycl-fp16"""
    if name and name.startswith("vulkan-"):
        return "vulkan"
    return DEFAULT_FLAVOR


FLAVOR_LABELS = {"sycl-fp16": "SYCL", "vulkan": "Vulkan"}


def _atomic_replace(src: Path, dst: Path):
    """原子替换：先复制到同目录临时文件，再 os.replace 覆盖。
    os.replace 对运行中文件是原子的（inode 替换），旧进程继续跑旧二进制，
    新启动用新二进制。避免 Text file busy (ETXTBSY)。"""
    tmp = dst.parent / f".{dst.name}.tmp.{os.getpid()}"
    shutil.copy2(str(src), str(tmp))
    os.chmod(str(tmp), 0o755)
    os.replace(str(tmp), str(dst))


def _is_valid_elf(path: Path) -> bool:
    """校验文件是有效的 ELF 可执行文件（前 4 字节 \x7fELF + 可执行位）"""
    try:
        if not os.access(str(path), os.X_OK):
            return False
        with open(str(path), "rb") as f:
            return f.read(4) == b"\x7fELF"
    except Exception:
        return False


def _sanitize_version(ver) -> str:
    """版本字符串清洗：只保留 [A-Za-z0-9._-]，非法字符替换为 _；空值报错拒绝"""
    if ver is None:
        raise HTTPException(400, "版本号为空")
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", str(ver)).strip("._")
    if not cleaned:
        raise HTTPException(400, f"版本号非法: {ver}")
    return cleaned


def _copy_entry(src: Path, dst: Path):
    """复制条目：符号链接重建链接本身，普通文件 copy2（不穿透链接）"""
    if src.is_symlink():
        target = os.readlink(str(src))
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        os.symlink(target, str(dst))
    elif src.is_file():
        shutil.copy2(str(src), str(dst))


def _restore_entry(src: Path, dst_dir: Path):
    """恢复条目：符号链接重建，普通文件原子替换"""
    dst = dst_dir / src.name
    if src.is_symlink():
        target = os.readlink(str(src))
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        os.symlink(target, str(dst))
    elif src.is_file():
        _atomic_replace(src, dst)


def _replace_from_dir(src_dir: Path, dst_dir: Path) -> int:
    """把 src_dir 中 llama-server*/lib* 条目替换到 dst_dir（符号链接重建，普通文件原子替换）
    返回替换条目数"""
    replaced = 0
    for f in sorted(src_dir.iterdir()):
        name = f.name
        if name.startswith("llama-server") or name.startswith("lib"):
            if f.is_symlink():
                target = os.readlink(str(f))
                dst = dst_dir / name
                if dst.exists() or dst.is_symlink():
                    dst.unlink()
                os.symlink(target, str(dst))
                replaced += 1
            elif f.is_file():
                _atomic_replace(f, dst_dir / name)
                replaced += 1
    return replaced


def _cleanup_residue(version: str):
    """回滚后清理 /app 下不属于目标版本备份集的 lib*/llama-server* 残留（先 diff 清单）"""
    dest = BIN_DIR / version
    if not dest.is_dir():
        return  # 旧格式单文件无法 diff 清单，跳过
    backup_names = {p.name for p in dest.iterdir() if p.name.startswith("lib") or p.name.startswith("llama-server")}
    app = _app_dir()
    for p in list(app.glob("lib*")) + list(app.glob("llama-server*")):
        if p.name not in backup_names:
            try:
                if p.is_symlink() or p.is_file():
                    p.unlink()
            except Exception:
                pass


def _app_dir() -> Path:
    return CURRENT_BIN.parent


def _current_set_files() -> list[Path]:
    """当前 /app 下的完整二进制集：llama-server* + lib*（含 SONAME 符号链接）"""
    app = _app_dir()
    files = []
    for p in sorted(app.glob("llama-server*")):
        files.append(p)
    for p in sorted(app.glob("lib*")):
        files.append(p)
    return files


def _backup_current_set(version: str) -> Path:
    """备份当前完整二进制集到 BIN_DIR/{version}/（幂等，保留符号链接）"""
    dest = BIN_DIR / version
    dest.mkdir(parents=True, exist_ok=True)
    # 已有备份则跳过（幂等），避免覆盖
    if (dest / "llama-server").exists() or (dest / "llama-server").is_symlink():
        return dest
    for f in _current_set_files():
        _copy_entry(f, dest / f.name)
    return dest


def _restore_set(version: str):
    """从 BIN_DIR/{version}/ 恢复完整集（含符号链接）；兼容旧格式单文件 BIN_DIR/llama-server-bXXX"""
    dest = BIN_DIR / version
    if dest.is_dir():
        restored = False
        for f in sorted(dest.iterdir()):
            if f.name.startswith("lib") or f.name.startswith("llama-server"):
                _restore_entry(f, _app_dir())
                restored = True
        if not restored:
            raise RuntimeError(f"备份目录 {dest} 为空")
        return
    # 旧格式: 单文件 BIN_DIR/llama-server-bXXX（只恢复 llama-server，找不到 .so 不报错）
    legacy = BIN_DIR / f"llama-server-{version}"
    if legacy.exists():
        _atomic_replace(legacy, CURRENT_BIN)
        return
    raise HTTPException(404, f"未找到版本 {version} 的备份")


def _upsert_setting(conn, key: str, value: str):
    """安全的 upsert：先 UPDATE，rowcount=0 再 INSERT（兼容所有表结构）"""
    cur = conn.execute("UPDATE app_settings SET value=? WHERE key=?", (value, key))
    if cur.rowcount == 0:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?,?)", (key, value))


def _write_active_version(version: str):
    """把当前生效版本写入卷内激活文件（BIN_DIR/active_version），
    供 entrypoint.sh 启动时从卷恢复（重建容器不丢升级成果）"""
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    (BIN_DIR / "active_version").write_text(version)


def _read_active_version() -> str:
    """读取卷内激活版本号；无则返回空串"""
    try:
        f = BIN_DIR / "active_version"
        if f.exists():
            v = f.read_text().strip()
            if v and (BIN_DIR / v).is_dir():
                return v
    except Exception:
        pass
    return ""


def _get_current_version() -> str:
    """检测当前 llama-server 版本，统一返回 bXXXXX 规范格式。
    兼容三种输出：
      - 新版 launcher: version: 0.3.0-dev (build 10622, ...) -> b10622
      - 新版: llama-server --version  b10246 (sha...)        -> b10246
      - 旧版: version: 10200 (5f55650a7) built_with...       -> b10200"""
    try:
        r = subprocess.run(
            [str(CURRENT_BIN), "--version"],
            capture_output=True, text=True, timeout=10,
        )
        output = (r.stdout + r.stderr).strip()
        import re
        for line in output.splitlines():
            line = line.strip()
            # 优先 bXXXXX 格式（launcher 也可能带 bXXXXX 字样）
            m = re.search(r"\bb\d+\b", line)
            if m:
                return m.group(0)
        # launcher 格式: version: 0.3.0-dev (build 10622, commit ...) -> b10622
        for line in output.splitlines():
            m = re.search(r"build\s+(\d+)", line)
            if m:
                return f"b{m.group(1)}"
        # 兼容旧版: version: 10200 (sha) -> 补 b 前缀
        for line in output.splitlines():
            m = re.search(r"version:\s*(\d+)", line)
            if m:
                return f"b{m.group(1)}"
        return "unknown"
    except Exception:
        return "unknown"


def _list_local_versions() -> list[dict]:
    """列出本地已安装的二进制版本（去重：当前版本只出现一次）
    每项含 version（显示名）/ dir（备份目录名，rollback 用）/ flavor"""
    versions = []
    active_name = _read_active_version() or ""
    current = _get_current_version()
    versions.append({
        "version": current, "dir": active_name or current, "active": True,
        "flavor": _dir_flavor(active_name) if active_name else DEFAULT_FLAVOR,
        "path": str(CURRENT_BIN),
    })
    seen = {current}
    if BIN_DIR.exists():
        # 新格式: 备份目录 BIN_DIR/bXXXX 或 BIN_DIR/vulkan-bXXXX
        for d in sorted(BIN_DIR.iterdir()):
            if d.is_dir() and d.name.startswith(("b", "vulkan-")) and d.name not in seen:
                versions.append({"version": d.name, "dir": d.name, "active": False,
                                 "flavor": _dir_flavor(d.name), "path": str(d)})
                seen.add(d.name)
        # 旧格式: 单文件 BIN_DIR/llama-server-bXXXX
        for f in sorted(BIN_DIR.glob("llama-server-b*")):
            ver = f.name.replace("llama-server-", "")
            if ver in seen:
                continue
            versions.append({"version": ver, "dir": ver, "active": False,
                             "flavor": DEFAULT_FLAVOR, "path": str(f)})
            seen.add(ver)
    return versions


def _fetch_releases(per_page: int = 10) -> list[dict]:
    """从 GitHub API 获取可用版本列表（走设置页代理，与模型下载一致）"""
    import urllib.request
    url = f"{GITHUB_API}?per_page={per_page}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "llama-studio/1.0",
        "Accept": "application/vnd.github+json",
    })
    # 走代理（复用 downloads.py 同款逻辑：每次请求独立 opener，不污染全局）
    opener = proxy.build_opener()
    with opener.open(req, timeout=30) as resp:
        data = json.loads(resp.read())

    result = []
    for rel in data:
        tag = rel["tag_name"]
        # 收集所有 flavor 的 asset（sycl-fp16 / vulkan 并存）
        assets = {}
        for flavor, pattern in FLAVOR_ASSETS.items():
            for a in rel["assets"]:
                if pattern in a["name"]:
                    assets[flavor] = {
                        "url": a["browser_download_url"],
                        "size": a["size"],
                        "size_human": _human_size(a["size"]),
                    }
                    break
        if assets:
            result.append({
                "version": tag,
                "assets": assets,
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
    flavor: str = DEFAULT_FLAVOR  # sycl-fp16 | vulkan


class RollbackRequest(BaseModel):
    version: str


class CleanupRequest(BaseModel):
    keep: int = 3  # 保留最近几个版本（不含 active）
    dry_run: bool = False  # True=只预览将删除列表，不实际删除


@router.post("/cleanup")
def engine_cleanup(body: CleanupRequest):
    """清理旧版本备份：保留 active 版本 + 最近 keep 个版本，其余删除。
    dry_run=True 时只返回将删除的列表不实际删除（前端确认弹窗用）。"""
    if body.keep < 0:
        raise HTTPException(400, "keep 不能为负")
    active = _read_active_version() or _get_current_version()

    def ver_num(name: str) -> int:
        m = re.match(r"(?:[a-z]+-)?b(\d+)", name)
        return int(m.group(1)) if m else 0

    # 新格式备份目录 BIN_DIR/bXXXX 或 BIN_DIR/vulkan-bXXXX
    dirs = [d for d in BIN_DIR.iterdir() if d.is_dir() and d.name.startswith(("b", "vulkan-"))]
    dirs.sort(key=lambda d: ver_num(d.name), reverse=True)
    keep = {active}
    for d in dirs:
        if len(keep) >= body.keep + 1:
            break
        keep.add(d.name)
    # 旧格式单文件 BIN_DIR/llama-server-bXXXX
    legacy = [f for f in BIN_DIR.glob("llama-server-b*")]

    if body.dry_run:
        deleted = [d.name for d in dirs if d.name not in keep]
        deleted += [f.name for f in legacy if f.name.replace("llama-server-", "") not in keep]
        return {"ok": True, "dry_run": True, "deleted": deleted, "kept": sorted(keep)}

    deleted = []
    for d in dirs:
        if d.name not in keep:
            shutil.rmtree(d, ignore_errors=True)
            deleted.append(d.name)
    for f in legacy:
        if f.name.replace("llama-server-", "") not in keep:
            f.unlink(missing_ok=True)
            deleted.append(f.name)
    return {"ok": True, "dry_run": False, "deleted": deleted, "kept": sorted(keep)}


class SwitchRequest(BaseModel):
    flavor: str = DEFAULT_FLAVOR  # sycl-fp16 | vulkan
    version: str | None = None  # 缺省自动选该后端已安装最高版本


@router.get("/backends")
def engine_backends():
    """后端总览：各推理后端（SYCL/Vulkan）的激活状态、已安装版本、可用版本
    前端设置页统一开关据此渲染"""
    installed = _list_local_versions()
    try:
        releases = _fetch_releases()
    except Exception:
        releases = []
    active_name = _read_active_version() or ""
    active_flavor = _dir_flavor(active_name) if active_name else DEFAULT_FLAVOR
    current_ver = _get_current_version()

    out = []
    for flavor, label in FLAVOR_LABELS.items():
        inst = [v for v in installed if v["flavor"] == flavor]
        avail = []
        for rel in releases:
            asset = rel.get("assets", {}).get(flavor)
            if asset:
                avail.append({
                    "version": rel["version"],
                    "size_human": asset["size_human"],
                    "published_at": rel.get("published_at", ""),
                })
        out.append({
            "flavor": flavor,
            "label": label,
            "active": flavor == active_flavor,
            "active_version": current_ver if flavor == active_flavor else "",
            "installed": inst,
            "available": avail,
        })
    return {
        "current": active_flavor,
        "current_version": current_ver,
        "backends": out,
    }


@router.post("/switch")
def engine_switch(body: SwitchRequest):
    """切换推理后端（SYCL <-> Vulkan）。version 缺省时自动选该后端已安装最高版本；
    本地已有备份则免下载切换，否则报错提示先安装"""
    flavor = body.flavor
    if flavor not in FLAVOR_ASSETS:
        raise HTTPException(400, f"不支持的构建类型: {flavor}（可选 {list(FLAVOR_ASSETS)}）")
    label = FLAVOR_LABELS.get(flavor, flavor)

    active_name = _read_active_version() or ""
    # 已激活同后端：直接返回
    if _dir_flavor(active_name) == flavor:
        if body.version is None or body.version == active_name:
            return {"ok": True, "already": True, "flavor": flavor,
                    "version": active_name, "message": f"当前已是 {label} 后端"}

    # 选版本：显式指定（校验本地已装）或该后端已安装最高版本
    installed = [v for v in _list_local_versions() if v["flavor"] == flavor and not v["active"]]
    if body.version:
        version = _sanitize_version(body.version)
        local_dir = BIN_DIR / _flavor_dir(flavor, version)
        if not (local_dir.is_dir() and (local_dir / "llama-server").exists()):
            raise HTTPException(404, f"{label} {version} 未安装，请先在版本列表安装")
    else:
        vers = sorted(
            (v["version"] for v in installed if v["version"].startswith("b")),
            key=lambda x: int(x[1:]) if x[1:].isdigit() else 0, reverse=True,
        )
        if not vers:
            raise HTTPException(400, f"{label} 后端未安装任何版本，请先在版本列表安装")
        version = vers[0]

    return engine_upgrade(UpgradeRequest(version=version, flavor=flavor))


@router.get("/version")
def engine_version():
    """当前版本 + 安装历史（含 flavor 构建标签）"""
    active_name = _read_active_version() or ""
    return {
        "current": _get_current_version(),
        "current_flavor": _dir_flavor(active_name) if active_name else "",
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
    """升级/切换二进制：备份 -> 下载（或免下载切换已存在目录）-> 解压 -> 校验 -> 替换
    flavor 支持 sycl-fp16（默认）/ vulkan；同版本同 flavor 已下载过时直接切换免下载"""
    import urllib.request
    import tarfile
    import tempfile

    version = _sanitize_version(body.version)
    if not version.startswith("b"):
        raise HTTPException(400, "版本号格式错误，应为 bXXXXX")
    flavor = body.flavor
    if flavor not in FLAVOR_ASSETS:
        raise HTTPException(400, f"不支持的构建类型: {flavor}（可选 {list(FLAVOR_ASSETS)}）")

    dir_name = _flavor_dir(flavor, version)
    current_ver = _sanitize_version(_get_current_version())
    active_name = _read_active_version() or ""

    # 同版本同 flavor 已下载过：免下载直接切换（/app 替换 + 激活）
    local_dir = BIN_DIR / dir_name
    if local_dir.is_dir() and (local_dir / "llama-server").exists() and dir_name != active_name:
        BIN_DIR.mkdir(parents=True, exist_ok=True)
        replaced = _replace_from_dir(local_dir, _app_dir())
        if not replaced:
            raise HTTPException(500, f"本地备份 {dir_name} 不完整，无法切换")
        _write_active_version(dir_name)
        with get_conn() as conn:
            _upsert_setting(conn, "engine_version", version)
            _upsert_setting(conn, "engine_last_upgrade", str(now()))
        return {"ok": True, "version": version, "flavor": flavor, "previous": current_ver,
                "switched": True, "message": f"已切换到 {FLAVOR_LABELS.get(flavor, flavor)} {version}（本地备份，免下载），需重启容器生效"}

    # 查找下载 URL
    try:
        releases = _fetch_releases()
    except Exception as e:
        raise HTTPException(500, f"获取版本列表失败: {e}")

    target = next((r for r in releases if r["version"] == version), None)
    if not target:
        raise HTTPException(404, f"未找到版本 {version}")
    asset = target.get("assets", {}).get(flavor)
    if not asset:
        raise HTTPException(404, f"版本 {version} 没有 {FLAVOR_LABELS.get(flavor, flavor)} 构建")

    # 步骤 1: 备份当前完整二进制集（llama-server* + lib*，含符号链接）
    # 目录名用 active 目录（vulkan-b10622）而非裸 build 号，避免覆盖另一 flavor 备份
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    backup_dir = _backup_current_set(active_name or current_ver)

    # 步骤 2: 下载
    download_url = asset["url"]
    tmp_dir = Path(tempfile.mkdtemp())
    archive_path = tmp_dir / f"llama-{flavor}-{version}.tar.gz"

    try:
        req = urllib.request.Request(download_url, headers={"User-Agent": "llama-studio/1.0"})
        # 走代理（同 downloads.py：独立 opener，不污染全局）
        opener = proxy.build_opener()
        with opener.open(req, timeout=600) as resp:
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

        # 步骤 4: ELF 校验（新版本 llama-server 是 launcher ~785KB，不能用大小阈值）
        if not _is_valid_elf(new_bin):
            raise RuntimeError(f"llama-server 不是有效的 ELF 可执行文件: {new_bin}")

        # 步骤 5: 替换完整二进制集（llama-server* + lib* 含 SONAME 符号链接）
        new_bin_dir = new_bin.parent
        replaced = _replace_from_dir(new_bin_dir, _app_dir())
        if not replaced:
            raise RuntimeError("解压目录中未找到需要替换的 llama-server*/lib*")

        # 记录到 DB + 卷内激活文件（重建容器不丢）
        # 确保新版本完整集在卷内有备份目录（entrypoint 恢复依赖 BIN_DIR/{dir_name}/）
        _backup_current_set(dir_name)
        _write_active_version(dir_name)
        with get_conn() as conn:
            _upsert_setting(conn, "engine_version", version)
            _upsert_setting(conn, "engine_last_upgrade", str(now()))

        return {"ok": True, "version": version, "flavor": flavor, "previous": current_ver,
                "switched": False, "message": f"已安装 {FLAVOR_LABELS.get(flavor, flavor)} {version}，需重启容器生效"}

    except Exception as e:
        # 自动回滚（恢复完整集 + 清理新版本残留）
        try:
            _restore_set(current_ver)
            _cleanup_residue(current_ver)
        except Exception: pass
        raise HTTPException(500, f"升级失败已回滚: {e}")
    finally:
        shutil.rmtree(str(tmp_dir), ignore_errors=True)


@router.post("/rollback")
def engine_rollback(body: RollbackRequest):
    """回滚到已安装的旧版本（恢复完整二进制集 + 清理残留）"""
    version = _sanitize_version(body.version)
    # 校验备份存在（新格式目录或旧格式单文件）
    dest = BIN_DIR / version
    legacy = BIN_DIR / f"llama-server-{version}"
    if not dest.is_dir() and not legacy.exists():
        raise HTTPException(404, f"未找到版本 {version} 的备份")

    current_ver = _sanitize_version(_get_current_version())
    active_name = _read_active_version() or ""
    # 备份当前版本（如果尚未备份，幂等）；目录名用 active 目录，避免覆盖另一 flavor 备份
    _backup_current_set(active_name or current_ver)

    try:
        _restore_set(version)
        _cleanup_residue(version)
        # 确保回滚目标版本在卷内有目录备份（兼容旧格式单文件回滚场景）
        _backup_current_set(version)
        _write_active_version(version)
        with get_conn() as conn:
            _upsert_setting(conn, "engine_version", version)
        return {"ok": True, "version": version, "previous": current_ver,
                "message": f"已回滚到 {version}，需重启容器生效"}
    except Exception as e:
        # 恢复原版本
        try:
            _restore_set(current_ver)
            _cleanup_residue(current_ver)
        except Exception: pass
        raise HTTPException(500, f"回滚失败已恢复: {e}")
