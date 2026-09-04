"""模型实例管理器 - 每个模型一个独立 llama-server 进程（per-model 上下文控制）

替代原 router 模式：不再用中心 router 统一管理，而是每个服务
启动一个独立 llama-server 单模型实例（--ctx-size 用预设值，真正 per-model），
WebUI 层按模型名反向代理到对应实例端口。

实例端口分配：BASE_PORT + sid（如 8081 起），重启后稳定（端口持久化到 DB）
"""
import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from app.config import settings
from app.database import get_conn, now

logger = logging.getLogger("instance-mgr")

BASE_PORT = int(os.environ.get("LLAMA_INSTANCE_BASE_PORT", "8081"))

# 内存态实例表: {sid: {"proc": Popen, "port": int, "started_at": int, "log_path": str}}
_instances: dict[int, dict] = {}
# 每实例活跃请求计数（M4 优雅停止用）
_active_requests: dict[int, int] = {}
# 每实例 draining 标志（置位后拒绝新请求）
_draining: set[int] = set()
_lock = None
# ===== 每实例并发闸（proxy 透传上限 = 实例 --parallel slot 数）=====
# 目的：防止突发请求全量灌给 llama-server 的 server_queue，造成“看起来无响应”。
# 由异步事件循环线程使用（uvicorn 单事件循环），与 _active_requests 正交。
_slot_guard: dict[int, asyncio.Semaphore] = {}
_slot_lock = threading.Lock()
# proxy 并发闸等待上限：并发超过实例 slot 数时，等待该秒数仍拿不到 slot 即 503
PROXY_SLOT_TIMEOUT = float(os.environ.get("LLAMA_PROXY_SLOT_TIMEOUT", "30"))


def _get_lock():
    global _lock
    if _lock is None:
        import threading
        _lock = threading.Lock()
    return _lock


def _get_slot_limit(name: str) -> int:
    """实例的生成并发槽位数 = preset parallel（llama-server 默认 1）"""
    preset = _preset_dict(name) or {}
    p = preset.get("parallel")
    try:
        return max(1, int(p)) if p not in (None, "") else 1
    except Exception:
        return 1


def _slot_guard_for(sid: int, name: str) -> asyncio.Semaphore:
    """取该实例的并发闸（惰性创建，limit = parallel slot 数）"""
    with _slot_lock:
        g = _slot_guard.get(sid)
        if g is None:
            g = asyncio.Semaphore(_get_slot_limit(name))
            _slot_guard[sid] = g
        return g


async def acquire_slot(sid: int, name: str, timeout: float | None = None) -> bool:
    """并发闸获取：timeout 内拿到返回 True；超时返回 False（不抛异常）。
    timeout=None 时用 PROXY_SLOT_TIMEOUT。"""
    g = _slot_guard_for(sid, name)
    t = PROXY_SLOT_TIMEOUT if timeout is None else timeout
    try:
        await asyncio.wait_for(g.acquire(), timeout=t)
        return True
    except asyncio.TimeoutError:
        return False


def release_slot(sid: int):
    """释放并发闸（实例已停/闸不存在时静默）"""
    g = _slot_guard.get(sid)
    if g is not None:
        g.release()


def slot_limit(name: str) -> int:
    """对外暴露并发上限（错误提示用）"""
    return _get_slot_limit(name)


# ===== 实例启动预热（②：把 flash-attn 内核 JIT 编译前移到启动期）=====
# IGC 编译 SYCL flash-attention 内核时崩溃（Internal Compiler Error / DEVICE_LOST）
# 是模型"无响应/崩溃"根因之一。预热在实例对外服务前用一次微推理把 flash/attention
# 内核编进持久 JIT 缓存（llama-studio-cache 卷），让 IGC 崩溃发生在启动预热阶段
# （由 self_heal 退避自愈收敛），而不是首个真实请求中途炸。嵌入模型不做生成注意力，跳过。
WARM_TIMEOUT = float(os.environ.get("LLAMA_INSTANCE_WARM_TIMEOUT", "180"))
WARM_PROBE_MAX_TOKENS = 2
# sid -> {"status": waiting|warming|ok|failed, "started": float, "done": float|None}
_warm: dict[int, dict] = {}


def _is_embedding_model(name: str) -> bool:
    n = (name or "").lower()
    return "embedding" in n or "embed" in n


def _warm_probe(sid: int, name: str, port: int):
    """后台预热线程：等 /health 就绪后跑一次微推理，强制 flash/attention 内核入 JIT 缓存"""
    import httpx
    started = time.time()
    _warm[sid] = {"status": "warming", "started": started, "done": None}
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            if _health_ok(port):
                break
            time.sleep(1)
        url = f"http://127.0.0.1:{port}/v1/chat/completions"
        payload = {
            "model": name,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": WARM_PROBE_MAX_TOKENS,
            "stream": False,
        }
        with httpx.Client(timeout=WARM_TIMEOUT) as c:
            r = c.post(url, json=payload)
        if r.status_code == 200:
            _warm[sid] = {"status": "ok", "started": started, "done": time.time()}
            logger.info("实例 %s 预热完成：flash/attention 内核已入 JIT 缓存（%.1fs）", name, time.time() - started)
        else:
            _warm[sid] = {"status": "failed", "started": started, "done": time.time()}
            logger.warning("实例 %s 预热探测返回 %s，跳过预热（不影响使用）", name, r.status_code)
    except httpx.TimeoutException:
        _warm[sid] = {"status": "failed", "started": started, "done": time.time()}
        logger.warning("实例 %s 预热超时（%.0fs），跳过预热", name, WARM_TIMEOUT)
    except Exception as e:
        _warm[sid] = {"status": "failed", "started": started, "done": time.time()}
        logger.warning("实例 %s 预热失败（进程可能崩/未就绪）: %s", name, e)


def warm_status(sid: int) -> dict | None:
    return _warm.get(sid)


def is_warming(sid: int) -> bool:
    w = _warm.get(sid)
    return bool(w and w.get("status") == "warming")


def _current_backend() -> str:
    """当前激活引擎后端：读卷内 active_version（vulkan-b10622 → vulkan；b10622 → sycl-fp16）"""
    try:
        ver = (Path(settings.data_dir) / "bin" / "active_version").read_text().strip()
        return "vulkan" if ver.startswith("vulkan-") else "sycl-fp16"
    except Exception:
        return "sycl-fp16"


def _list_devices() -> list[dict]:
    """解析 llama-server --list-devices（后端无关），返回 [{id, name, is_discrete, total_mib, free_mib}]
    独显/核显按设备名标注：Arc → 独显，Iris/Xe → 核显。
    """
    import re as _re
    llama_bin = os.environ.get("LLAMA_SERVER_BIN", "/app/llama-server")
    devices: list[dict] = []
    try:
        if not os.path.isfile(llama_bin):
            return devices
        r = subprocess.run([llama_bin, "--list-devices"], capture_output=True, text=True, timeout=15)
        output = (r.stdout or "") + (r.stderr or "")
        pattern = _re.compile(r"(SYCL|Vulkan)(\d+):\s*(.+?)\s*\((\d+)\s*MiB,\s*(\d+)\s*MiB\s*free\)")
        for m in pattern.finditer(output):
            raw_name = m.group(3).strip()
            devices.append({
                "id": f"{m.group(1)}{m.group(2)}",
                "name": raw_name,
                "is_discrete": "Arc" in raw_name,
                "total_mib": int(m.group(4)),
                "free_mib": int(m.group(5)),
            })
    except Exception:
        pass
    return devices


def _resolve_device(role: str) -> str:
    """语义设备角色 → 当前后端的具体设备名（动态解析 --list-devices）。
    role: auto（优先独显）/ discrete（独显）/ integrated（核显）。
    构建可能同时含 SYCL+Vulkan 后端，这里按当前引擎过滤（SYCL 引擎只取 SYCLx，Vulkan 引擎只取 Vulkanx）。
    返回 '' 表示不传 --device（交给 llama.cpp 自动选）。
    """
    role = (role or "auto").strip().lower()
    backend = _current_backend()  # sycl-fp16 / vulkan
    prefix = "Vulkan" if backend == "vulkan" else "SYCL"
    devices = [d for d in _list_devices() if d["id"].startswith(prefix)]
    discrete = [d for d in devices if d["is_discrete"]]
    integrated = [d for d in devices if not d["is_discrete"]]
    if role == "discrete":
        return discrete[0]["id"] if discrete else (devices[0]["id"] if devices else "")
    if role == "integrated":
        return integrated[0]["id"] if integrated else ""
    # auto：优先独显，无独显则首个可用设备
    return discrete[0]["id"] if discrete else (devices[0]["id"] if devices else "")


def _preset_dict(model_name: str) -> dict | None:
    """读取模型预设（含 ctx_size/parallel/device 等）。
    device 为语义角色（auto/discrete/integrated），后端无关，启动时按当前后端解析。"""
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM model_presets WHERE model_name=?",
                (model_name,),
            ).fetchone()
            return dict(row) if row else None
    except Exception:
        return None


def _build_args(sid: int, name: str, model_path: str) -> list[str]:
    """构建 llama-server 单模型实例启动参数（ctx 用预设值，per-model 生效）"""
    preset = _preset_dict(name) or {}
    llama_bin = os.environ.get("LLAMA_SERVER_BIN", "/app/llama-server")
    args = [
        llama_bin,
        "--model", model_path,
        "--host", "127.0.0.1",
        "--port", str(_port_for(sid)),
        "--alias", name,
        "--metrics",  # 暴露 /metrics（监控页聚合每模型吞吐/MTP/请求队列）
    ]
    # embedding 模型自动加 --embeddings（否则 /v1/embeddings 返回 501）
    if "embedding" in name.lower() or "embed" in name.lower():
        args += ["--embeddings"]
    # ctx-size：预设值（核心：per-model 上下文）
    ctx = preset.get("ctx_size") or 8192
    args += ["--ctx-size", str(ctx)]
    # RoPE/YaRN 长上下文缩放（Qwen 社区建议 >32K 必须启用，不能只加 ctx-size）
    rs = preset.get("rope_scaling")
    if rs:  # none/linear/yarn
        args += ["--rope-scaling", str(rs)]
        if preset.get("rope_scale"):
            args += ["--rope-scale", str(preset["rope_scale"])]
        if preset.get("yarn_orig_ctx"):
            args += ["--yarn-orig-ctx", str(preset["yarn_orig_ctx"])]
    # 思考（Reasoning）：--reasoning on/off/auto + --reasoning-budget N（控制思维链长度）
    reasoning = preset.get("reasoning")
    if reasoning:
        args += ["--reasoning", str(reasoning)]
        if preset.get("reasoning_budget") is not None:
            args += ["--reasoning-budget", str(preset["reasoning_budget"])]
        # 思考强度（Reasoning Effort）：--reasoning-effort <level>（minimal/low/medium/high/xhigh/max）
        effort = preset.get("reasoning_effort")
        if effort:
            args += ["--reasoning-effort", str(effort)]
    # 采样/性能参数
    if preset.get("temp") is not None:
        args += ["--temp", str(preset["temp"])]
    if preset.get("threads"):
        args += ["--threads", str(preset["threads"])]
    if preset.get("batch_size"):
        args += ["--batch-size", str(preset["batch_size"])]
    if preset.get("ubatch_size"):
        args += ["--ubatch-size", str(preset["ubatch_size"])]
    if preset.get("parallel"):
        args += ["--parallel", str(preset["parallel"])]
    if preset.get("cache_type_k"):
        args += ["--cache-type-k", str(preset["cache_type_k"])]
    if preset.get("cache_type_v"):
        args += ["--cache-type-v", str(preset["cache_type_v"])]
    if preset.get("flash_attn"):
        args += ["--flash-attn", "on"]
    # MoE 专家 offload 到 CPU（attention 全 GPU，专家跑 CPU）
    # 实测（Qwen3.6-35B-A3B）：相比按层 offload，速度 +24%（19.5 t/s）且显存省 2.5GB
    # cpu_moe_layers：0/空=全部专家层（--cpu-moe）；N>0=仅前 N 层（--n-cpu-moe N，省显存/平衡速度）
    if preset.get("cpu_moe"):
        _cmoe_n = preset.get("cpu_moe_layers") or 0
        if _cmoe_n and _cmoe_n > 0:
            args += ["--n-cpu-moe", str(_cmoe_n)]
        else:
            args += ["--cpu-moe"]
    # MTP 多 token 预测（投机解码加速）：需用户自备 MTP 模型文件
    # --spec-type draft-mtp + --spec-draft-model <path> + --spec-draft-n-max N
    if preset.get("mtp"):
        args += ["--spec-type", "draft-mtp"]
        mtp_model = preset.get("mtp_model") or ""
        if mtp_model:
            # 支持相对 /models 路径
            if not mtp_model.startswith("/"):
                mtp_model = f"/models/{mtp_model.lstrip('/')}"
            args += ["--spec-draft-model", mtp_model]
        # MTP 预测 token 数（llama.cpp 默认 3；越大加速越多但接受率下降）
        mtp_n_max = preset.get("mtp_n_max")
        if mtp_n_max:
            args += ["--spec-draft-n-max", str(mtp_n_max)]
        # MTP 草稿 KV cache 量化（默认 f16 浪费显存，建议 q8_0；仅非空才传，空则用默认）
        if preset.get("spec_draft_type_k"):
            args += ["--spec-draft-type-k", str(preset["spec_draft_type_k"])]
        if preset.get("spec_draft_type_v"):
            args += ["--spec-draft-type-v", str(preset["spec_draft_type_v"])]
    if preset.get("jinja"):
        args += ["--jinja"]
    if preset.get("n_gpu_layers") is not None:
        args += ["--n-gpu-layers", str(preset["n_gpu_layers"])]
    if preset.get("mmap") == 0:
        args += ["--no-mmap"]
    # 设备：语义角色（auto/discrete/integrated）→ 按当前后端动态解析具体设备名（SYCL0/Vulkan1）
    dev = preset.get("device") or "auto"
    resolved = _resolve_device(dev)
    if resolved:
        args += ["--device", resolved]
    # mmproj
    mmproj = preset.get("mmproj") or ""
    if not mmproj:
        # 自动检测同目录 mmproj
        try:
            p = Path(model_path)
            found = sorted(p.parent.glob("*mmproj*.gguf"))
            if found:
                mmproj = str(found[0])
        except Exception:
            pass
    if mmproj:
        args += ["--mmproj", mmproj]
    # extra_args（sampling 等）
    try:
        extra = json.loads(preset.get("extra_args") or "{}")
    except Exception:
        extra = {}
    sampling = extra.pop("sampling", None) if isinstance(extra, dict) else None
    if isinstance(sampling, dict):
        smap = {
            "top_k": "top-k", "top_p": "top-p", "min_p": "min-p",
            "typical_p": "typical-p", "repeat_penalty": "repeat-penalty",
            "presence_penalty": "presence-penalty",
            "frequency_penalty": "frequency-penalty",
            "seed": "seed", "mirostat": "mirostat",
            "mirostat_lr": "mirostat-lr", "mirostat_ent": "mirostat-ent",
        }
        for k, rk in smap.items():
            v = sampling.get(k)
            if v in (None, ""):
                continue
            args += [f"--{rk}", str(v)]
    if isinstance(extra, dict):
        for k, v in extra.items():
            args += [f"--{k}", str(v)]
    return args


def _port_for(sid: int) -> int:
    """实例端口：BASE_PORT + sid（持久化稳定）"""
    return BASE_PORT + int(sid) - 1


def begin_request(sid: int) -> bool:
    """标记请求开始（draining 时拒绝）。返回是否允许进入"""
    if sid in _draining:
        return False
    _active_requests[sid] = _active_requests.get(sid, 0) + 1
    return True


def end_request(sid: int):
    """标记请求结束"""
    _active_requests[sid] = max(0, _active_requests.get(sid, 0) - 1)


def active_requests(sid: int) -> int:
    return _active_requests.get(sid, 0)


def is_draining(sid: int) -> bool:
    return sid in _draining


def mark_draining(sid: int):
    _draining.add(sid)


def clear_draining(sid: int):
    _draining.discard(sid)


def _port_in_use(port: int) -> bool:
    """检查端口是否已被监听（不依赖 ss/lsof，用 socket 探测）"""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _find_pid_on_port(port: int) -> int | None:
    """通过 /proc/net/tcp inode 匹配找到监听端口的进程 pid"""
    try:
        import struct
        hex_port = f":{port:04X}"
        # 收集所有 socket inode -> pid（从 /proc/*/fd 解析）
        inode_pid = {}
        for fd_dir in Path("/proc").glob("[0-9]*/fd"):
            pid = int(fd_dir.parent.name)
            try:
                for fd in fd_dir.iterdir():
                    try:
                        tgt = os.readlink(fd)
                    except OSError:
                        continue
                    if tgt.startswith("socket:["):
                        ino = tgt[8:-1]
                        if ino not in inode_pid:
                            inode_pid[ino] = pid
            except OSError:
                continue
        # 在 /proc/net/tcp 找监听端口对应的 inode
        with open("/proc/net/tcp") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) < 10:
                    continue
                local, state = parts[1], parts[3]
                if state == "0A" and local.endswith(hex_port):
                    ino = parts[9]
                    return inode_pid.get(ino)
    except Exception:
        return None
    return None


def _health_ok(port: int) -> bool:
    """探测实例健康（/health 返回 200）"""
    try:
        import httpx
        with httpx.Client(timeout=3) as c:
            r = c.get(f"http://127.0.0.1:{port}/health")
        return r.status_code == 200
    except Exception:
        return False


def _proc_alive(proc) -> bool:
    """Popen 进程是否存活"""
    return proc is not None and proc.poll() is None


def _proc_started_at(pid) -> int | None:
    """按 pid 解析进程启动时间戳（ps etime → epoch），失败返回 None"""
    if not pid:
        return None
    try:
        import subprocess as _sp
        out = _sp.run(["ps", "-o", "etimes=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
        etime_s = int(out.stdout.strip())
        return int(time.time()) - etime_s
    except Exception:
        return None


def _force_release_port(port: int, wait_s: float = 5.0):
    """确保端口释放：反复按端口找 pid 并强杀（最多 wait_s 秒）"""
    deadline = time.time() + wait_s
    while time.time() < deadline and _port_in_use(port):
        pid = _find_pid_on_port(port)
        if pid:
            if _is_zombie(pid):
                _reap_zombie(pid)
            else:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        time.sleep(0.5)


# ========== HTTP 级探活（带 TTL 缓存） ==========
HEALTH_TTL = 3.0  # 秒：同一实例探活结果缓存时长，避免列表轮询频繁发请求
_health_cache: dict[int, dict] = {}  # sid -> {"ts": float, "ok": bool, "latency_ms": int}


def _check_health(port: int, sid: int | None = None) -> dict:
    """探测实例 /health，带 TTL 缓存（sid=None 时强制探测不缓存）"""
    if sid is not None:
        cached = _health_cache.get(sid)
        if cached and time.time() - cached["ts"] < HEALTH_TTL:
            return cached
    ok, latency_ms = False, 0
    try:
        import httpx
        t0 = time.time()
        with httpx.Client(timeout=2) as c:
            r = c.get(f"http://127.0.0.1:{port}/health")
        ok = r.status_code == 200
        latency_ms = int((time.time() - t0) * 1000)
    except Exception:
        ok = False
    res = {"ts": time.time(), "ok": ok, "latency_ms": latency_ms}
    if sid is not None:
        _health_cache[sid] = res
    return res


def _env() -> dict:
    """实例运行环境：完整 LD_LIBRARY_PATH（oneAPI）"""
    env = os.environ.copy()
    cur = env.get("LD_LIBRARY_PATH", "")
    if "oneapi" not in cur:
        libs = []
        for pat in ("/opt/intel/oneapi/*/lib", "/opt/intel/oneapi/compiler/*/lib"):
            libs += sorted(__import__("glob").glob(pat))
        env["LD_LIBRARY_PATH"] = ":".join(libs + [cur]).strip(":")
    env["ZES_ENABLE_SYSMAN"] = env.get("ZES_ENABLE_SYSMAN", "1")
    env["GGML_SYCL_ENABLE_FLASH_ATTN"] = env.get("GGML_SYCL_ENABLE_FLASH_ATTN", "1")
    # 默认关闭 host pinned memory（llama.cpp #26789）：iGPU 上触发 memcpy OOM
    # （UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY），核显/独显双 GPU 实测需置 0
    env["GGML_SYCL_ENABLE_HOST_PINNED_MEM"] = env.get("GGML_SYCL_ENABLE_HOST_PINNED_MEM", "0")
    return env


def start_instance(sid: int, name: str, model_path: str) -> dict:
    """启动模型实例（已启动则返回现有）

    防孤儿残留：内存态为空但端口被占时——
    1) 端口健康 → 复用已有实例（重建内存态，避免重复拉起撞端口）
    2) 端口不健康 → 清理残留进程再启动
    """
    with _get_lock():
        inst = _instances.get(sid)
        if inst and _proc_alive(inst["proc"]):
            return {"ok": True, "status": "running", "port": inst["port"], "pid": inst["proc"].pid}

        port = _port_for(sid)
        # 端口已被占（孤儿实例/WebUI 重启后残留）
        if _port_in_use(port):
            if _health_ok(port):
                # 已有健康实例在跑 → 复用，重建内存态（started_at 用真实启动时间，
                # 避免孤儿实例永远处于自愈保护窗口）
                pid = _find_pid_on_port(port)
                _instances[sid] = {"proc": None, "pid": pid, "port": port,
                                  "started_at": _proc_started_at(pid) or int(time.time()),
                                  "log_path": str(Path(settings.data_dir) / "instances" / f"{name}.log")}
                logger.info("实例复用 sid=%s name=%s port=%d pid=%s（检测到已运行）", sid, name, port, pid)
                return {"ok": True, "status": "running", "port": port, "pid": pid,
                        "reused": True, "detail": "检测到已有实例在运行，已复用"}
            # 端口被占但不健康 → 清理残留
            pid = _find_pid_on_port(port)
            if pid:
                if _is_zombie(pid):
                    # 僵尸进程无法 kill，reap 后端口自动释放
                    logger.warning("清理僵尸实例 sid=%s port=%d pid=%s（reap）", sid, port, pid)
                    _reap_zombie(pid)
                    time.sleep(1)
                else:
                    logger.warning("清理残留实例 sid=%s port=%d pid=%s（不健康）", sid, port, pid)
                    try:
                        os.kill(pid, signal.SIGTERM)
                        time.sleep(2)
                        if _port_in_use(port):
                            os.kill(pid, signal.SIGKILL)
                            time.sleep(1)
                    except ProcessLookupError:
                        pass

        args = _build_args(sid, name, model_path)
        log_dir = Path(settings.data_dir) / "instances"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{name}.log"
        logf = open(log_path, "a")
        proc = subprocess.Popen(
            args, stdout=logf, stderr=subprocess.STDOUT, env=_env(),
        )
        _instances[sid] = {"proc": proc, "port": port, "started_at": int(time.time()), "log_path": str(log_path)}
        logger.info("实例启动 sid=%s name=%s port=%d pid=%d", sid, name, port, proc.pid)
        # 生成模型启动预热：后台线程编译 flash/attention 内核入 JIT 缓存
        if not _is_embedding_model(name):
            threading.Thread(target=_warm_probe, args=(sid, name, port), name=f"warm-{sid}", daemon=True).start()
        return {"ok": True, "status": "starting", "port": port, "pid": proc.pid, "log": str(log_path)}


def stop_instance(sid: int, graceful: bool = True, drain_timeout: float = 30.0) -> dict:
    """停止模型实例（TERM，超时 KILL）——支持复用的孤儿实例（proc=None）

    graceful=True 时：先置 draining（新请求被拒），等待在途请求排空（最多 drain_timeout 秒），
    再 TERM/KILL；排空超时则直接终止（在途流会断，属可接受的强制场景）。
    """
    with _get_lock():
        inst = _instances.get(sid)
        if not inst:
            # 实例不在内存态（已停止/孤儿但已清理），无需等待
            _draining.discard(sid)
            _active_requests.pop(sid, None)
            _slot_guard.pop(sid, None)
            _warm.pop(sid, None)
            return {"ok": True, "status": "not_running"}
        if graceful:
            mark_draining(sid)
            # 等待在途请求结束
            waited = 0.0
            while _active_requests.get(sid, 0) > 0 and waited < drain_timeout:
                time.sleep(0.5)
                waited += 0.5
            if _active_requests.get(sid, 0) > 0:
                logger.warning("优雅停止超时 sid=%s 仍有 %d 个在途请求，强制终止", sid, _active_requests.get(sid, 0))
        clear_draining(sid)
        _active_requests.pop(sid, None)
        _slot_guard.pop(sid, None)
        _warm.pop(sid, None)

        inst = _instances.pop(sid, None)
        proc = inst.get("proc")
        port = inst.get("port") or _port_for(sid)
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=15)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            # kill 后进程可能残留为僵尸（父进程未 reap），立即回收
            try:
                if _is_zombie(proc.pid):
                    _reap_zombie(proc.pid)
            except Exception:
                pass
        # 孤儿实例（仅 pid）：按 pid 杀
        pid = inst.get("pid")
        if pid and proc is None:
            try:
                os.kill(pid, signal.SIGTERM)
                for _ in range(15):
                    time.sleep(1)
                    if not _pid_alive(pid):
                        break
                else:
                    os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        # 兜底：确保端口真正释放（防止残留进程占端口导致下次复用脏实例）
        if _port_in_use(port):
            logger.warning("实例停止后端口 %d 仍被占用，按端口清理残留", port)
            _force_release_port(port)
        logger.info("实例停止 sid=%s pid=%s port=%d", sid, pid, port)
        return {"ok": True, "status": "stopped", "pid": pid}


def _pid_alive(pid) -> bool:
    """按 pid 判断进程存活"""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _is_zombie(pid) -> bool:
    """判断进程是否为僵尸（Z）：僵尸占端口且无法 kill，必须视为已死"""
    if not pid:
        return False
    try:
        with open(f"/proc/{pid}/stat") as f:
            # 格式: pid (comm) state ...；comm 可能含空格，从最后一个 ) 后取 state
            line = f.read()
        state = line[line.rfind(")") + 2:line.rfind(")") + 3]
        return state == "Z"
    except (FileNotFoundError, PermissionError, IndexError, ValueError):
        return False


def _reap_zombie(pid) -> bool:
    """回收僵尸进程（reap），成功返回 True"""
    if not pid:
        return False
    try:
        waited, _ = os.waitpid(pid, os.WNOHANG)
        return waited == pid
    except (ChildProcessError, ProcessLookupError):
        # 不是子进程或已回收
        return False
    except Exception:
        return False


def _zombie_harvest_once():
    """收割一轮僵尸：遍历内存态实例 + 全量扫描 PPID=self 的僵尸

    - 内存态实例：Popen 调 poll（reap），孤儿 pid 调 waitpid
    - 全量扫描：覆盖内存态丢失但僵尸仍在的场景（WebUI 重启后
      旧 llama-server 变僵尸且不在 _instances 中）
    """
    for sid, inst in list(_instances.items()):
        try:
            proc = inst.get("proc")
            pid = inst.get("pid")
            if proc is not None:
                # poll() 会 reap 已退出子进程（释放僵尸）
                proc.poll()
            elif pid and _is_zombie(pid):
                _reap_zombie(pid)
                # 僵尸已回收：移除记录，端口随之释放
                _instances.pop(sid, None)
                logger.info("回收僵尸实例 sid=%s pid=%s", sid, pid)
        except Exception:
            pass
    # 全量扫描：/proc 中所有 PPID==self 的僵尸（不限于内存态记录）
    self_pid = os.getpid()
    try:
        for proc_dir in Path("/proc").glob("[0-9]*"):
            try:
                stat_path = proc_dir / "stat"
                if not stat_path.exists():
                    continue
                stat = stat_path.read_text(errors="ignore")
                # 格式: pid (comm) state ppid ...；comm 可能含空格括号
                rp = stat.rfind(")")
                if rp < 0:
                    continue
                fields = stat[rp + 2:].split()
                if len(fields) < 2:
                    continue
                state, ppid = fields[0], fields[1]
                if state == "Z" and ppid.isdigit() and int(ppid) == self_pid:
                    pid = int(proc_dir.name)
                    if _reap_zombie(pid):
                        logger.info("全量收割僵尸 pid=%s（PPID=self）", pid)
            except Exception:
                continue
    except Exception:
        pass


def start_zombie_harvester(interval: float = 15.0):
    """后台僵尸收割线程（防端口泄漏/脏实例复用）"""
    import threading

    def _loop():
        while True:
            try:
                _zombie_harvest_once()
            except Exception:
                pass
            time.sleep(interval)

    t = threading.Thread(target=_loop, name="zombie-harvester", daemon=True)
    t.start()
    logger.info("僵尸收割线程已启动（周期 %ss）", interval)
    return t


def instance_status(sid: int) -> dict:
    """查询实例状态（三态：running/degraded/stopped + 健康数据）

    - running:  进程存活 且 /health 通（HTTP 级探活，TTL 缓存）
    - degraded: 进程存活 但 /health 不通（进程卡死/未就绪）
    - starting: 进程刚启动（<15s）未就绪
    - stopped:  进程已退出/无实例

    兼容孤儿实例：内存态无记录时探测端口（WebUI 重启/外部启动场景）
    """
    base = {"running": False, "state": "stopped", "port": _port_for(sid), "pid": None}
    inst = _instances.get(sid)
    if not inst:
        # 孤儿实例探测：端口被健康实例占用则视为运行中
        port = _port_for(sid)
        if _port_in_use(port) and _health_ok(port):
            pid = _find_pid_on_port(port)
            # 真实启动时间：从 ps etime 解析（孤儿实例不能简单用当前时间，
            # 否则永远处于自愈保护窗口内）
            started_at = _proc_started_at(pid) or int(time.time())
            inst = {"proc": None, "pid": pid, "port": port,
                    "started_at": started_at,
                    "log_path": str(Path(settings.data_dir) / "instances" / f"sid{sid}.log")}
            _instances[sid] = inst
        else:
            return base
    proc = inst.get("proc")
    alive = _proc_alive(proc) if proc is not None else _pid_alive(inst.get("pid"))
    if not alive or (inst.get("pid") and _is_zombie(inst.get("pid"))):
        # 进程已退出或僵尸（僵尸占端口且无法 kill）→ 清理记录
        _instances.pop(sid, None)
        if inst.get("pid") and _is_zombie(inst.get("pid")):
            _reap_zombie(inst.get("pid"))
        return base
    port = inst["port"]
    h = _check_health(port, sid)
    pid = proc.pid if proc is not None else inst.get("pid")
    # 进程刚启动（<15s）未就绪 → 显示 starting，不归为 degraded（避免加载中误报降级）
    started_at = inst.get("started_at") or 0
    if not h["ok"] and time.time() - started_at < 15:
        state = "starting"
    else:
        state = "running" if h["ok"] else "degraded"
    return {
        "running": True,  # 进程存活（旧字段，兼容现有调用方）
        "state": state,
        "port": port,
        "pid": pid,
        "started_at": started_at,
        "health_latency_ms": h["latency_ms"],
        "last_health_at": h["ts"],
        "health_ok": h["ok"],
    }


def instance_state(sid: int) -> str:
    """快速取状态字符串（不解析全量）"""
    return instance_status(sid)["state"]


def all_instances() -> dict[int, dict]:
    """返回所有运行中实例 {sid: {port, pid}}"""
    out = {}
    for sid in list(_instances.keys()):
        st = instance_status(sid)
        if st["running"]:
            out[sid] = st
    return out


def touch_usage(sid: int):
    """记录实例被调用（空闲自动卸载用）"""
    try:
        with get_conn() as conn:
            conn.execute(
                "UPDATE services SET last_used_at=?, updated_at=? WHERE id=?",
                (int(time.time()), now(), sid),
            )
    except Exception:
        pass


def url_for(sid: int) -> str:
    """实例的 base url（WebUI 反代目标）"""
    return f"http://127.0.0.1:{_port_for(sid)}"
