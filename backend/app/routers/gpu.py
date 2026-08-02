"""GPU / 系统监控 API - 容器内环境"""
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter
from app.config import settings

router = APIRouter()


def _run(cmd: list[str], timeout: int = 10) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return ""


@router.get("")
def gpu_status():
    """GPU 状态：优先 xpu-smi，兜底 /sys/class/drm"""
    out = _run(["xpu-smi", "stats"])
    if out.strip():
        return {"source": "xpu-smi", "raw": out}
    # 兜底：读取 /sys/class/drm 下的 GPU 信息
    devices = []
    for p in sorted(Path("/sys/class/drm").glob("card*/device")):
        try:
            name = (p / "uevent").read_text().splitlines()
            vendor = ""
            for line in name:
                if "PCI_ID" in line:
                    vendor = line.split("=")[1]
            devices.append({"path": str(p.parent), "pci_id": vendor})
        except Exception:
            continue
    return {"source": "sysfs", "devices": devices}


@router.get("/system")
def system_status():
    """系统资源概览"""
    import os

    mem = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3
    avail = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1024**3

    disk = shutil.disk_usage("/")
    return {
        "memory_total_gb": round(mem, 1),
        "memory_avail_gb": round(avail, 1),
        "disk_total_gb": round(disk.total / 1024**3, 1),
        "disk_free_gb": round(disk.free / 1024**3, 1),
        "model_dir": settings.model_dir,
        "model_dir_size_gb": round(_dir_size_gb(Path(settings.model_dir)), 2),
    }


def _dir_size_gb(path: Path) -> float:
    if not path.exists():
        return 0
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except Exception:
                pass
    return total / 1024**3
