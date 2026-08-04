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


def _parse_xpu_smi_table() -> dict[int, dict]:
    """解析 xpu-smi table 输出，按设备块返回 {gpu_id: {pci_bdf, name, mem_used_mib, mem_total_mib, mem_util_pct}}
    table 格式每设备两行：
      |   N   ...   | 0000:xx:xx.x  Off | ... |
      | N/A  N/A  26W / 117W | 28MiB / 16288MiB | ... |
    """
    dump = _run(["xpu-smi"], timeout=10)
    devices: dict[int, dict] = {}
    if not dump:
        return devices
    lines = dump.splitlines()
    cur_id = None
    for line in lines:
        line_s = line.strip()
        # 设备块首行：|   0   Off | 0000:00:02.0  Off | ...
        m_id = re.match(r'\|\s*(\d+)\s+\S+\s+\|\s*(0000:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f])', line_s, re.IGNORECASE)
        if m_id:
            cur_id = int(m_id.group(1))
            devices.setdefault(cur_id, {"pci_bdf": m_id.group(2), "name": "Intel GPU", "mem_used_mib": None, "mem_total_mib": None, "mem_util_pct": None})
            continue
        if cur_id is None:
            continue
        # 设备块第二行：| N/A  26W / 117W | 28MiB / 16288MiB | ...
        m_mem = re.search(r'(\d+)MiB\s*/\s*(\d+)MiB', line_s)
        if m_mem and devices[cur_id]["mem_used_mib"] is None:
            used_mib = int(m_mem.group(1))
            total_mib = int(m_mem.group(2))
            devices[cur_id]["mem_used_mib"] = used_mib
            devices[cur_id]["mem_total_mib"] = total_mib
            if total_mib > 0:
                devices[cur_id]["mem_util_pct"] = round(used_mib / total_mib * 100)
            # 设备名
            if 'Arc' in line_s or 'Iris' in line_s or 'Graphics' in line_s:
                m_name = re.search(r'((?:Intel\(R\)|Intel)\s+[\w\(\)\s,\'-]+(?:Graphics|Arc|Iris)[\w\(\)\s,\'-]*)', line_s)
                if m_name:
                    devices[cur_id]["name"] = m_name.group(1).strip()
    return devices


def _parse_xpu_smi_query() -> list[dict]:
    """解析 xpu-smi 输出，返回设备列表
    XE1 硬件不支持 memory.utilization / clocks.current.soc / temperature，
    核显（id=0）的 power/energy 也是 N/A，需逐字段容错。
    双 GPU（核显 id=0 + 独显 id=1）分别查询。
    """
    devices = []
    table_devices = _parse_xpu_smi_table()
    # 遍历 GPU id（0=核显, 1=独显），每个独立查询，N/A 字段容错
    for gpu_id in (0, 1):
        out = _run([
            "xpu-smi", "--query-gpu=memory.used,memory.total,power.draw,power.limit,energy.consumed",
            "--format=csv,noheader", f"--id={gpu_id}"
        ], timeout=10)
        if not out.strip():
            continue
        parts = [p.strip() for p in out.strip().split(",")]
        if len(parts) < 5:
            continue

        def _f(v):
            try:
                return float(v)
            except (ValueError, TypeError):
                return None

        mem_used = _f(parts[0])
        mem_total = _f(parts[1])
        power_draw = _f(parts[2])
        power_limit = _f(parts[3])
        energy_j = _f(parts[4])
        if mem_used is None or mem_total is None:
            continue  # 该设备查不到内存，跳过

        t = table_devices.get(gpu_id, {})
        name = t.get("name") or "Intel GPU"
        pci_bdf = t.get("pci_bdf") or ""
        mem_util_pct = t.get("mem_util_pct")

        mem_total_mib = int(round(mem_total))
        mem_used_mib = int(round(mem_used))
        if mem_util_pct is None and mem_total_mib > 0:
            mem_util_pct = round(mem_used_mib / mem_total_mib * 100)

        devices.append({
            "id": gpu_id,
            "pci_bdf": pci_bdf or None,
            "name": name,
            "memory_used_mib": mem_used_mib,
            "memory_total_mib": mem_total_mib,
            "memory_util_pct": mem_util_pct,
            "power_draw_w": round(power_draw, 1) if power_draw is not None else None,
            "power_limit_w": round(power_limit, 1) if power_limit is not None else None,
            "frequency_mhz": None,  # XE1 不支持 clocks.current.soc
            "energy_consumed_j": int(round(energy_j)) if energy_j is not None else None,
            "temperature_c": None,  # XE1 硬件限制，N/A
        })
    return devices


def _model_memory_by_device() -> dict[int, int]:
    """统计每个 GPU 设备上 llama-server 进程的真实内存占用（RSS MiB）

    通过进程 args 中的 --device 参数匹配设备；无法识别设备的进程
    归入 -1（用于汇总展示）。
    """
    result = {0: 0, 1: 0, -1: 0}
    try:
        ps_out = _run(["ps", "-eo", "pid,rss,args"], timeout=5)
        for line in ps_out.strip().splitlines()[1:]:
            if "llama-server" not in line:
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            try:
                rss_kb = int(parts[1])
            except ValueError:
                continue
            args = " ".join(parts[2:])
            dev = -1
            if "--device" in args:
                try:
                    # 按空白切分后定位 --device 的下一个参数（进程路径可能含空格，用 rsplit 找最后一段）
                    arg_tokens = args.split()
                    di = arg_tokens.index("--device")
                    dev_str = arg_tokens[di + 1]
                    m = re.search(r"(\d+)", dev_str)
                    if m:
                        dev = int(m.group(1))
                except (ValueError, IndexError):
                    dev = -1
            result[dev] = result.get(dev, 0) + int(rss_kb // 1024)
    except Exception:
        pass
    return result


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


@router.get("/selectable")
def selectable_gpus():
    """返回可选 GPU 设备列表（解析 llama-server --list-devices，回退 /dev/dri 扫描）"""
    import os
    import re
    import glob

    # 尝试调用 llama-server --list-devices
    llama_bin = os.environ.get("LLAMA_SERVER_BIN", "/app/llama-server")
    if os.path.isfile(llama_bin):
        try:
            r = subprocess.run(
                [llama_bin, "--list-devices"],
                capture_output=True, text=True, timeout=15,
            )
            # --list-devices 输出到 stdout，warning 行可能到 stderr
            output = r.stdout + r.stderr
            # 匹配: SYCL0: Intel(R) Arc(TM) A770M Graphics (15473 MiB, 15473 MiB free)
            pattern = re.compile(
                r"SYCL(\d+):\s*(.+?)\s*\((\d+)\s*MiB,\s*(\d+)\s*MiB\s*free\)"
            )
            gpus = []
            for m in pattern.finditer(output):
                idx = int(m.group(1))
                raw_name = m.group(2).strip()
                total_mib = int(m.group(3))
                free_mib = int(m.group(4))
                # 标注独显/核显
                if "Arc" in raw_name:
                    label = "独显"
                elif "Iris" in raw_name or "Xe" in raw_name:
                    label = "核显"
                else:
                    label = "GPU"
                gpus.append({
                    "id": f"SYCL{idx}",
                    "sycl_index": idx,
                    "name": f"{raw_name} ({label})",
                    "total_mib": total_mib,
                    "free_mib": free_mib,
                })
            if gpus:
                return gpus
        except Exception:
            pass

    # 回退：扫描 /dev/dri
    gpus = []
    for card in sorted(glob.glob("/dev/dri/card*")):
        idx = card.split("card")[-1]
        render = f"/dev/dri/renderD{128 + int(idx)}"
        gpus.append({
            "id": f"SYCL{idx}",
            "sycl_index": None,
            "name": f"GPU card{idx} (回退)",
            "card": card,
            "render": render,
        })
    return gpus


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
        mem_by_dev = _model_memory_by_device()

        # 附加设备真实模型内存占用 + 集显标记
        for dev in devices:
            dev["model_memory_mib"] = mem_by_dev.get(dev["id"], 0)
            # 核显（id=0 / PCI 00:02.0）共享系统内存，显存统计不可信
            dev["is_integrated"] = dev["id"] == 0 or "00:02.0" in (dev.get("pci_bdf") or "")
        total_model_mib = sum(v for k, v in mem_by_dev.items() if k >= 0)

        return {
            "source": "xpu-smi",
            "devices": devices,
            "processes": processes,
            "inference": inference,
            "total_model_memory_mib": total_model_mib,
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
