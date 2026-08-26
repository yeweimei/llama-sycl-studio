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
    注意：--device 用 SYCL 编号（SYCL0=独显），与 xpu-smi 设备号相反。
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


def _model_memory_by_device_sycl1() -> int:
    """统计跑在核显（--device SYCL1+）上的 llama-server 进程 RSS 总和（MiB）

    xpu-smi 进程表不显示核显上的进程，需从 ps 按启动参数匹配。
    注意 SYCL 编号与 xpu-smi 设备号相反：SYCL0=独显，SYCL1+=核显。
    """
    total = 0
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
            m = re.search(r"--device\s+(SYCL)?(\d+)", args)
            if not m:
                continue
            sycl_n = int(m.group(2))
            if sycl_n >= 1:  # SYCL1+ = 核显（SYCL0 = 独显）
                total += int(rss_kb // 1024)
    except Exception:
        pass
    return total


def _parse_xpu_smi_processes() -> list[dict]:
    """从 xpu-smi 输出解析 GPU 进程列表，fallback ps

    实际格式：| GPU | PID | Type | Process Name | GPU Memory Usage |
    → 保留 gpu_id，核显共享内存统计不可信，用进程真实占用替代
    """
    processes = []
    dump = _run(["xpu-smi"], timeout=10)
    if dump:
        in_proc = False
        for line in dump.splitlines():
            if "Processes" in line:
                in_proc = True
                continue
            if in_proc and line.strip():
                # xpu-smi 表格是空格对齐（非竖线分隔），按列位置切片：
                # |   GPU     PID    Type    Process Name       GPU Memory Usage |
                # 列: GPU(1-6) PID(7-14) Type(15-21) Name(22-45) Mem(46+)
                if "+" in line or "---" in line:
                    continue
                line_s = line.rstrip()
                # 列边界（实测）：GPU pos6, PID pos13-15, Type pos20, Name pos27+
                gpu_s = line_s[1:7].strip()
                pid_s = line_s[7:17].strip()
                type_s = line_s[17:23].strip()
                name_s = line_s[23:48].strip()
                mem_s = line_s[48:].strip()
                if not pid_s.isdigit():
                    continue
                mem_mib = 0
                m = re.search(r'(\d+)', mem_s)
                if m:
                    mem_mib = int(m.group(1))
                processes.append({
                    "gpu_id": int(gpu_s) if gpu_s.lstrip('-').isdigit() else -1,
                    "pid": int(pid_s),
                    "type": type_s,
                    "name": name_s,
                    "memory_mib": mem_mib,
                })

    if not processes:
        # fallback: ps 过滤 llama-server（无法分 GPU，归 gpu_id=-1）
        ps_out = _run(["ps", "-eo", "pid,comm,rss"], timeout=5)
        for line in ps_out.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 3:
                pid_s, name_s, rss_s = parts[0], parts[1], parts[2]
                if "llama" in name_s.lower():
                    try:
                        processes.append({
                            "gpu_id": -1,
                            "pid": int(pid_s),
                            "name": name_s,
                            "memory_mib": int(int(rss_s) // 1024),
                            "type": "",
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
    """返回可选 GPU 设备列表（解析 llama-server --list-devices，按当前引擎过滤，回退 /dev/dri 扫描）"""
    import os
    import re
    import glob

    from app.instance_mgr import _current_backend

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
            #      或 Vulkan0: Intel(R) Iris(R) Xe Graphics (ADL GT2) (46938 MiB, 42244 MiB free)
            pattern = re.compile(
                r"(SYCL|Vulkan)(\d+):\s*(.+?)\s*\((\d+)\s*MiB,\s*(\d+)\s*MiB\s*free\)"
            )
            prefix = "Vulkan" if _current_backend() == "vulkan" else "SYCL"
            gpus = []
            for m in pattern.finditer(output):
                if m.group(1) != prefix:
                    continue
                backend = m.group(1)
                idx = int(m.group(2))
                raw_name = m.group(3).strip()
                total_mib = int(m.group(4))
                free_mib = int(m.group(5))
                # 标注独显/核显
                if "Arc" in raw_name:
                    label = "独显"
                elif "Iris" in raw_name or "Xe" in raw_name:
                    label = "核显"
                else:
                    label = "GPU"
                gpus.append({
                    "id": f"{backend}{idx}",
                    "sycl_index": idx,
                    "backend": backend.lower(),
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


def _list_devices_backend() -> list[dict]:
    """后端无关设备列表（主数据源）：解析 llama-server --list-devices
    返回 [{id, backend, name, is_discrete, memory_total_mib, memory_free_mib, memory_used_mib}]
    构建可能同时含 SYCL+Vulkan 后端，这里按当前引擎过滤（SYCL 引擎只显示 SYCLx，Vulkan 引擎只显示 Vulkanx）。
    """
    from app.instance_mgr import _current_backend
    llama_bin = os.environ.get("LLAMA_SERVER_BIN", "/app/llama-server")
    devices = []
    if os.path.isfile(llama_bin):
        try:
            r = subprocess.run([llama_bin, "--list-devices"], capture_output=True, text=True, timeout=15)
            output = (r.stdout or "") + (r.stderr or "")
            pattern = re.compile(r"(SYCL|Vulkan)(\d+):\s*(.+?)\s*\((\d+)\s*MiB,\s*(\d+)\s*MiB\s*free\)")
            prefix = "Vulkan" if _current_backend() == "vulkan" else "SYCL"
            for m in pattern.finditer(output):
                if m.group(1) != prefix:
                    continue
                raw_name = m.group(3).strip()
                total_mib = int(m.group(4))
                free_mib = int(m.group(5))
                devices.append({
                    "id": f"{m.group(1)}{m.group(2)}",
                    "backend": m.group(1).lower(),
                    "name": raw_name,
                    "is_discrete": "Arc" in raw_name,
                    "memory_total_mib": total_mib,
                    "memory_free_mib": free_mib,
                    "memory_used_mib": max(total_mib - free_mib, 0),
                })
        except Exception:
            pass
    return devices


def _model_memory_by_backend_device() -> dict[str, int]:
    """进程真实内存占用（RSS MiB）按 --device 设备名归集（后端无关：SYCLx/Vulkanx）"""
    result: dict[str, int] = {}
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
            m = re.search(r"--device\s+((?:SYCL|Vulkan)\d+)", args)
            if m:
                dev = m.group(1)
                result[dev] = result.get(dev, 0) + int(rss_kb // 1024)
    except Exception:
        pass
    return result


def _xpu_smi_sensors() -> dict[str, dict]:
    """xpu-smi 传感器（功耗/能耗/真实显存），按设备名（Arc=独显 / Iris=核显）关联到角色
    返回 {role: {pci_bdf, name, power_draw_w, power_limit_w, energy_consumed_j, memory_used_mib, memory_total_mib}}
    ⚠️ xpu-smi 的 memory 是物理 GPU 真实显存（独显准确）；核显共享内存 used 可能 > total（不可信，调用方需校验）
    """
    sensors: dict[str, dict] = {}
    if not shutil.which("xpu-smi"):
        return sensors
    table = _parse_xpu_smi_table()  # 物理 gpu_id → {name, pci_bdf}
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

        t = table.get(gpu_id, {})
        name = t.get("name") or ""
        pci = t.get("pci_bdf") or ""
        # 角色判定：优先用 PCI 地址（Intel 核显固定 00:02.0），其次设备名（Arc=独显 / Iris·Xe·UHD=核显）
        # ⚠️ 不能只看 name：部分 xpu-smi 版本不输出设备名（兜底 "Intel GPU"），此时按 PCI 判断
        is_igpu = "00:02.0" in pci or any(k in name for k in ("Iris", "Xe", "UHD", "HD Graphics"))
        role = "integrated" if is_igpu else "discrete"
        pd = _f(parts[2])
        pl = _f(parts[3])
        ej = _f(parts[4])
        mu = _f(parts[0])
        mt = _f(parts[1])
        sensors[role] = {
            "pci_bdf": t.get("pci_bdf") or None,
            "name": name,
            "power_draw_w": round(pd, 1) if pd is not None else None,
            "power_limit_w": round(pl, 1) if pl is not None else None,
            "energy_consumed_j": int(round(ej)) if ej is not None else None,
            # 物理 GPU 真实显存（独显准确；核显共享内存 used 可能异常，调用方校验后使用）
            "memory_used_mib": int(round(mu)) if mu is not None else None,
            "memory_total_mib": int(round(mt)) if mt is not None else None,
        }
    return sensors


@router.get("")
def gpu_status():
    """GPU 状态（后端无关）：设备列表来自 llama-server --list-devices（当前后端），
    进程占用按 --device 参数归集，功耗/能耗来自 xpu-smi 按设备角色关联。"""
    devices = _list_devices_backend()
    if not devices:
        return {
            "source": "unavailable",
            "devices": [],
            "processes": [],
            "inference": {},
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "error": "llama-server --list-devices 无输出",
        }
    proc_mem = _model_memory_by_backend_device()
    sensors = _xpu_smi_sensors()
    # 进程明细（后端无关：按 --device 参数识别设备）
    processes = []
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
            m = re.search(r"--device\s+((?:SYCL|Vulkan)\d+)", " ".join(parts[2:]))
            processes.append({
                "gpu_id": m.group(1) if m else -1,
                "pid": int(parts[0]),
                "name": "llama-server",
                "memory_mib": int(rss_kb // 1024),
                "type": "C",
            })
    except Exception:
        pass
    for dev in devices:
        dev_id = dev["id"]
        dev["model_memory_mib"] = proc_mem.get(dev_id, 0)
        role = "discrete" if dev["is_discrete"] else "integrated"
        s = sensors.get(role, {})
        dev["pci_bdf"] = s.get("pci_bdf")
        dev["power_draw_w"] = s.get("power_draw_w")
        dev["power_limit_w"] = s.get("power_limit_w")
        dev["energy_consumed_j"] = s.get("energy_consumed_j")
        dev["frequency_mhz"] = None
        dev["temperature_c"] = None
        dev["is_integrated"] = not dev["is_discrete"]
        # ⚠️ 显存修正：SYCL 后端 --list-devices 的 free 可能是假的（SYSMAN 未启用时
        # ext_intel_free_memory 不支持 → use total as free → used 恒为 0）。
        # 优先用 xpu-smi 物理显存覆盖（0 <= used <= total 才算合理）；核显共享内存 used>total 时丢弃。
        x_mu = s.get("memory_used_mib")
        x_mt = s.get("memory_total_mib")
        if x_mu is not None and x_mt is not None and 0 <= x_mu <= x_mt:
            dev["memory_used_mib"] = x_mu
            dev["memory_total_mib"] = x_mt
            dev["memory_free_mib"] = max(x_mt - x_mu, 0)
        dev["memory_util_pct"] = round(dev["memory_used_mib"] / dev["memory_total_mib"] * 100) if dev["memory_total_mib"] > 0 else 0
    inference = _query_inference_metrics()
    return {
        "source": "list-devices+xpu-smi",
        "devices": devices,
        "processes": processes,
        "inference": inference,
        "total_model_memory_mib": sum(proc_mem.values()),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
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
