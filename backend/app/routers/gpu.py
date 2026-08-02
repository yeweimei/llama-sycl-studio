"""GPU / 系统监控 API - 容器内环境"""
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter
from app.config import settings

router = APIRouter()


def _run(cmd: list[str], timeout: int = 10) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return ""


def _parse_xpu_smi_query() -> list[dict]:
    """解析 xpu-smi --query-gpu 输出，返回设备列表"""
    out = _run([
        "xpu-smi", "--query-gpu=memory.used,memory.total,memory.utilization,power.draw,power.limit,clocks.current.soc,energy.consumed",
        "--format=csv,noheader", "--id=0"
    ], timeout=10)
    if not out.strip():
        return []
    # 输出形如: 11890.953125, 16288, 73.00, 26.26, 117.00, 600, 4397.05
    parts = [p.strip() for p in out.strip().split(",")]
    if len(parts) < 7:
        return []
    try:
        mem_used = float(parts[0])
        mem_total = float(parts[1])
        mem_util = float(parts[2])
        power_draw = float(parts[3])
        power_limit = float(parts[4])
        freq_mhz = float(parts[5])
        energy_j = float(parts[6])
    except (ValueError, IndexError):
        return []

    # 获取设备名和 PCI BDF
    name = "Intel GPU"
    pci_bdf = ""
    dump = _run(["xpu-smi"], timeout=10)
    if dump:
        for line in dump.splitlines():
            line = line.strip()
            # 匹配设备名行
            m = re.search(r'(Intel\(R\)\s*[\w\(\)\s]+)', line)
            if m and not name.startswith("Intel(R)"):
                name = m.group(1).strip()
            # 匹配 PCI BDF
            m2 = re.search(r'(0000:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f])', line, re.IGNORECASE)
            if m2:
                pci_bdf = m2.group(1)

    mem_total_mib = int(round(mem_total))
    mem_used_mib = int(round(mem_used))
    return [{
        "id": 0,
        "pci_bdf": pci_bdf or None,
        "name": name,
        "memory_used_mib": mem_used_mib,
        "memory_total_mib": mem_total_mib,
        "memory_util_pct": int(round(mem_util)),
        "power_draw_w": round(power_draw, 1),
        "power_limit_w": round(power_limit, 1),
        "frequency_mhz": int(round(freq_mhz)) if freq_mhz > 0 else None,
        "energy_consumed_j": int(round(energy_j)),
        "temperature_c": None,  # XE1 硬件限制，N/A
    }]


def _parse_xpu_smi_processes() -> list[dict]:
    """从 xpu-smi 输出解析 GPU 进程列表，fallback ps"""
    processes = []
    dump = _run(["xpu-smi"], timeout=10)
    if dump:
        in_proc = False
        for line in dump.splitlines():
            if "Processes" in line:
                in_proc = True
                continue
            if in_proc and line.strip():
                parts = [p.strip() for p in line.split("|")]
                # 期望格式: | 0 | compute | 1213 | llama-server | 7486 MiB |
                if len(parts) >= 6:
                    try:
                        pid = int(parts[3])
                        name = parts[4]
                        mem_str = parts[5]
                        mem_mib = 0
                        m = re.search(r'(\d+)', mem_str)
                        if m:
                            mem_mib = int(m.group(1))
                        processes.append({"pid": pid, "name": name, "memory_mib": mem_mib})
                    except (ValueError, IndexError):
                        continue

    if not processes:
        # fallback: ps 过滤 llama-server
        ps_out = _run(["ps", "-eo", "pid,comm,rss"], timeout=5)
        for line in ps_out.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 3:
                pid_s, name_s, rss_s = parts[0], parts[1], parts[2]
                if "llama" in name_s.lower():
                    try:
                        processes.append({
                            "pid": int(pid_s),
                            "name": name_s,
                            "memory_mib": int(int(rss_s) // 1024),
                        })
                    except (ValueError, IndexError):
                        continue
    return processes


def _query_inference_metrics() -> dict:
    """查询 router /metrics 获取推理指标"""
    result = {"requests_processing": 0, "prompt_tps": 0.0, "predicted_tps": 0.0}
    try:
        # 获取已加载模型列表
        with httpx.Client(timeout=5.0) as c:
            r = c.get(f"{settings.router_url.rstrip('/')}/v1/models")
            if r.status_code != 200:
                return result
            data = r.json()
        models = data.get("data", []) if isinstance(data, dict) else data
        loaded = []
        for m in models:
            status = m.get("status")
            status_val = status.get("value", "") if isinstance(status, dict) else (status or "")
            if status_val.lower() in ("loaded", "ready", "ok"):
                loaded.append(m.get("id", ""))

        if not loaded:
            return result

        total_req = 0
        total_prompt_tps = 0.0
        total_pred_tps = 0.0
        base = settings.router_url.rstrip("/")
        with httpx.Client(timeout=5.0) as c:
            for model_id in loaded:
                try:
                    r = c.get(f"{base}/metrics?model={model_id}")
                    if r.status_code != 200:
                        continue
                    text = r.text
                    for line in text.splitlines():
                        if line.startswith("llamacpp:requests_processing"):
                            v = line.split()[-1]
                            try:
                                total_req += int(float(v))
                            except ValueError:
                                pass
                        elif line.startswith("llamacpp:prompt_tokens_seconds"):
                            v = line.split()[-1]
                            try:
                                total_prompt_tps += float(v)
                            except ValueError:
                                pass
                        elif line.startswith("llamacpp:predicted_tokens_seconds"):
                            v = line.split()[-1]
                            try:
                                total_pred_tps += float(v)
                            except ValueError:
                                pass
                except Exception:
                    continue
        result = {
            "requests_processing": total_req,
            "prompt_tps": round(total_prompt_tps, 1),
            "predicted_tps": round(total_pred_tps, 1),
        }
    except Exception:
        pass
    return result


@router.get("")
def gpu_status():
    """GPU 状态：结构化输出（xpu-smi）"""
    try:
        if not shutil.which("xpu-smi"):
            return {
                "source": "unavailable",
                "devices": [],
                "processes": [],
                "inference": {},
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "error": "xpu-smi not found",
            }

        devices = _parse_xpu_smi_query()
        if not devices:
            return {
                "source": "unavailable",
                "devices": [],
                "processes": [],
                "inference": {},
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "error": "xpu-smi query returned no data",
            }

        processes = _parse_xpu_smi_processes()
        inference = _query_inference_metrics()

        return {
            "source": "xpu-smi",
            "devices": devices,
            "processes": processes,
            "inference": inference,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
    except Exception as e:
        return {
            "source": "unavailable",
            "devices": [],
            "processes": [],
            "inference": {},
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "error": str(e),
        }


@router.get("/system")
def system_status():
    """系统资源概览"""
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
