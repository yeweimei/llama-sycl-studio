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
    """启动模型实例（已启动则返回现有）"""
    with _get_lock():
        inst = _instances.get(sid)
        if inst and inst["proc"].poll() is None:
            return {"ok": True, "status": "running", "port": inst["port"], "pid": inst["proc"].pid}

        port = _port_for(sid)
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


def stop_instance(sid: int) -> dict:
    """停止模型实例（TERM，超时 KILL）"""
    with _get_lock():
        inst = _instances.pop(sid, None)
        if not inst:
            return {"ok": True, "status": "not_running"}
        proc = inst["proc"]
        try:
            proc.terminate()
            proc.wait(timeout=15)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        logger.info("实例停止 sid=%s", sid)
        return {"ok": True, "status": "stopped", "pid": proc.pid}


def instance_status(sid: int) -> dict:
    """查询实例状态（running/stopped + port/pid）"""
    inst = _instances.get(sid)
    if not inst:
        return {"running": False, "port": _port_for(sid), "pid": None}
    proc = inst["proc"]
    if proc.poll() is not None:
        # 进程已退出，清理
        _instances.pop(sid, None)
        return {"running": False, "port": _port_for(sid), "pid": None}
    return {"running": True, "port": inst["port"], "pid": proc.pid, "started_at": inst["started_at"]}


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
