"""模型实例管理器 - 每个模型一个独立 llama-server 进程（per-model 上下文控制）

替代原 router 模式：不再用中心 router 统一管理，而是每个服务
启动一个独立 llama-server 单模型实例（--ctx-size 用预设值，真正 per-model），
WebUI 层按模型名反向代理到对应实例端口。

实例端口分配：BASE_PORT + sid（如 8081 起），重启后稳定（端口持久化到 DB）
"""
import json
import logging
import os
import shutil
import signal
import subprocess
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


def _get_lock():
    global _lock
    if _lock is None:
        import threading
        _lock = threading.Lock()
    return _lock


def _preset_dict(model_name: str) -> dict | None:
    """读取模型预设（含 ctx_size/parallel/device 等）"""
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM model_presets WHERE model_name=?", (model_name,)
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
    ]
    # ctx-size：预设值（核心：per-model 上下文）
    ctx = preset.get("ctx_size") or 8192
    args += ["--ctx-size", str(ctx)]
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
    if preset.get("jinja"):
        args += ["--jinja"]
    if preset.get("n_gpu_layers") is not None:
        args += ["--n-gpu-layers", str(preset["n_gpu_layers"])]
    if preset.get("mmap") == 0:
        args += ["--no-mmap"]
    # 设备
    dev = preset.get("device") or "SYCL0"
    if dev and dev != "0":
        dev = dev if str(dev).startswith(("SYCL", "CPU")) else f"SYCL{dev}"
        args += ["--device", str(dev)]
    # mmproj
    mmproj = preset.get("mmproj") or ""
    if not mmproj:
        # 自动检测同目录 mmproj
        try:
            p = Path(model_path)
            found = sorted(p.parent.glob("mmproj*.gguf"))
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
                # 已有健康实例在跑 → 复用，重建内存态
                pid = _find_pid_on_port(port)
                _instances[sid] = {"proc": None, "pid": pid, "port": port,
                                  "started_at": int(time.time()), "log_path": str(Path(settings.data_dir) / "instances" / f"{name}.log")}
                logger.info("实例复用 sid=%s name=%s port=%d pid=%s（检测到已运行）", sid, name, port, pid)
                return {"ok": True, "status": "running", "port": port, "pid": pid,
                        "reused": True, "detail": "检测到已有实例在运行，已复用"}
            # 端口被占但不健康 → 清理残留
            pid = _find_pid_on_port(port)
            if pid:
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
        return {"ok": True, "status": "starting", "port": port, "pid": proc.pid, "log": str(log_path)}


def stop_instance(sid: int, graceful: bool = True, drain_timeout: float = 30.0) -> dict:
    """停止模型实例（TERM，超时 KILL）——支持复用的孤儿实例（proc=None）

    graceful=True 时：先置 draining（新请求被拒），等待在途请求排空（最多 drain_timeout 秒），
    再 TERM/KILL；排空超时则直接终止（在途流会断，属可接受的强制场景）。
    """
    with _get_lock():
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

        inst = _instances.pop(sid, None)
        if not inst:
            return {"ok": True, "status": "not_running"}
        proc = inst.get("proc")
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=15)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            logger.info("实例停止 sid=%s pid=%s", sid, proc.pid)
            return {"ok": True, "status": "stopped", "pid": proc.pid}
        # 孤儿实例（仅 pid）：按 pid 杀
        pid = inst.get("pid")
        if pid:
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
            logger.info("孤儿实例停止 sid=%s pid=%s", sid, pid)
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


def instance_status(sid: int) -> dict:
    """查询实例状态（三态：running/degraded/stopped + 健康数据）

    - running:  进程存活 且 /health 通（HTTP 级探活，TTL 缓存）
    - degraded: 进程存活 但 /health 不通（进程卡死/未就绪）
    - stopped:  进程已退出/无实例
    """
    base = {"running": False, "state": "stopped", "port": _port_for(sid), "pid": None}
    inst = _instances.get(sid)
    if not inst:
        return base
    proc = inst.get("proc")
    alive = _proc_alive(proc) if proc is not None else _pid_alive(inst.get("pid"))
    if not alive:
        _instances.pop(sid, None)
        return base
    port = inst["port"]
    h = _check_health(port, sid)
    pid = proc.pid if proc is not None else inst.get("pid")
    return {
        "running": True,  # 进程存活（旧字段，兼容现有调用方）
        "state": "running" if h["ok"] else "degraded",
        "port": port,
        "pid": pid,
        "started_at": inst.get("started_at"),
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
