"""服务管理 API - router 模型池管理（替代旧容器管理）"""
import json
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

from app import router_client
from app.database import get_conn, now
from app.config import settings
from app.routers.stats import _record_stats

router = APIRouter()


# ── 对话内容日志（chat_api_logs，最近 1000 条）──
CHAT_LOG_MAX_ROWS = 1000


def _chat_log_create(model_name: str, stream: int, user_message: str) -> int:
    """创建一条 running 状态的对话日志，返回 log_id"""
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO chat_api_logs (model_name, stream, user_message, status, created_at) "
                "VALUES (?,?,?,?,?)",
                (model_name, stream, (user_message or "")[:4000], "running", int(time.time())),
            )
            log_id = cur.lastrowid
        _chat_log_prune()
        return log_id
    except Exception:
        return 0


def _chat_log_append(log_id: int, response_piece: str = "", thinking_piece: str = "", flush: bool = False):
    """追加 response/thinking 片段；flush=True 时立即落库，否则靠 next 调用落库"""
    if not log_id:
        return
    try:
        with get_conn() as conn:
            if response_piece or thinking_piece:
                conn.execute(
                    "UPDATE chat_api_logs SET response = response || ?, thinking = thinking || ? WHERE id=?",
                    (response_piece, thinking_piece, log_id),
                )
            if flush:
                conn.execute(
                    "UPDATE chat_api_logs SET status='done', finished_at=? WHERE id=?",
                    (int(time.time()), log_id),
                )
    except Exception:
        pass


def _chat_log_finish(log_id: int, ok: bool = True, status_code: int = 200,
                     prompt_tokens: int = 0, completion_tokens: int = 0,
                     total_ms: int = 0, error: str = ""):
    """请求结束：更新状态/计数/耗时"""
    if not log_id:
        return
    try:
        with get_conn() as conn:
            conn.execute(
                "UPDATE chat_api_logs SET status=?, status_code=?, prompt_tokens=?, "
                "completion_tokens=?, total_ms=?, error=?, finished_at=? WHERE id=?",
                ("error" if not ok else "done", status_code,
                 prompt_tokens, completion_tokens, total_ms, (error or "")[:500],
                 int(time.time()), log_id),
            )
    except Exception:
        pass


def _chat_log_prune():
    """清理超出最近 1000 条的最旧记录"""
    try:
        with get_conn() as conn:
            conn.execute(
                "DELETE FROM chat_api_logs WHERE id NOT IN "
                "(SELECT id FROM chat_api_logs ORDER BY id DESC LIMIT ?)",
                (CHAT_LOG_MAX_ROWS,),
            )
    except Exception:
        pass


def _last_user_message(messages: list) -> str:
    """提取最后一条 user 消息文本（支持多模态 content）"""
    if not messages:
        return ""
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content", "")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                parts = []
                for it in c:
                    if isinstance(it, dict) and it.get("type") == "text":
                        parts.append(str(it.get("text", "")))
                return " ".join(parts)
            return str(c)
    return ""


class ServiceCreate(BaseModel):
    name: Optional[str] = None   # 可选：为空时自动推导为 router ID
    model_path: str
    args: dict = {}
    gpu_id: Optional[str] = None
    idle_unload_min: int = 0     # 空闲自动卸载分钟，0=一直保持


class ServiceUpdate(BaseModel):
    args: Optional[dict] = None
    name: Optional[str] = None
    model_path: Optional[str] = None
    gpu_id: Optional[str] = None
    idle_unload_min: Optional[int] = None


def _model_path_from_loaded(loaded_info) -> dict:
    """从 router /models 返回的 loaded 详情中提取 id -> 实际模型文件路径 映射
    （llama.cpp router 的模型 ID 可能是目录名，实际文件路径在 status.args 的 --model 里）"""
    result = {}
    items = loaded_info
    if isinstance(items, dict):
        items = items.get("data", [])
    if not isinstance(items, list):
        return result
    for m in items:
        mid = m.get("model", m.get("id", ""))
        if not mid:
            continue
        st = m.get("status") if isinstance(m.get("status"), dict) else {}
        args = st.get("args") or []
        if isinstance(args, str):
            args = args.split()
        for i, a in enumerate(args):
            if a == "--model" and i + 1 < len(args):
                result[mid] = args[i + 1]
                break
    return result


def _supports_chat(model_name: str, loaded_detail: dict) -> bool:
    """判断模型是否支持对话（embedding/rerank 类模型不支持）

    判定依据：模型名含 embedding/embed/rerank/bge 关键词
    （不能用 --embeddings 参数判断：router 全局 --embeddings，
    所有子进程都会继承该参数）
    """
    nl = (model_name or "").lower()
    if any(k in nl for k in ("embedding", "embed-", "rerank", "bge-", "bge_")):
        return False
    return True


def _device_label_map() -> dict:
    """解析 llama-server --list-devices，返回 {SYCLx: 设备标签} 映射（带 30s 缓存）
    如 {'SYCL0': 'Arc A770M (独显)', 'SYCL1': 'Iris Xe (核显)'}
    通用性：任意 Intel 设备按名称自动标注（Arc=独显 / Iris|Xe=核显），
    不依赖固定的 SYCL 序号映射。
    """
    import subprocess
    import re
    import os
    import time

    _cache_key = "_dev_map_cache"
    _ts_key = "_dev_map_ts"
    now = time.time()
    if getattr(_device_label_map, _cache_key, None) and now - getattr(_device_label_map, _ts_key, 0) < 30:
        return getattr(_device_label_map, _cache_key)

    result = {}
    llama_bin = os.environ.get("LLAMA_SERVER_BIN", "/app/llama-server")
    try:
        if os.path.isfile(llama_bin):
            # 确保 oneAPI 库路径在 LD_LIBRARY_PATH 中（llama-server 依赖 libsvml 等）
            env = os.environ.copy()
            cur_ld = env.get("LD_LIBRARY_PATH", "")
            if "oneapi" not in cur_ld:
                import glob as _glob
                oneapi_libs = ":".join(sorted(_glob.glob("/opt/intel/oneapi/*/lib")) +
                                        _glob.glob("/opt/intel/oneapi/compiler/*/lib"))
                env["LD_LIBRARY_PATH"] = f"{oneapi_libs}:{cur_ld}".strip(":")
            r = subprocess.run([llama_bin, "--list-devices"], capture_output=True, text=True, timeout=15, env=env)
            output = r.stdout + r.stderr
            pattern = re.compile(r"SYCL(\d+):\s*(.+?)\s*\(\d+\s*MiB")
            for m in pattern.finditer(output):
                idx = m.group(1)
                raw_name = m.group(2).strip()
                if "Arc" in raw_name:
                    label = f"{raw_name} (独显)"
                elif "Iris" in raw_name or "Xe" in raw_name:
                    label = f"{raw_name} (核显)"
                else:
                    label = raw_name
                result[f"SYCL{idx}"] = label
    except Exception:
        pass
    setattr(_device_label_map, _cache_key, result)
    setattr(_device_label_map, _ts_key, now)
    return result


def _extract_proc_info(loaded_detail: dict) -> dict:
    """从 router /models 返回的 loaded_detail 中解析进程级信息"""
    import subprocess
    import re

    info = {"port": None, "device": None, "device_label": None, "pid": None, "loaded_at": None}
    if not loaded_detail:
        return info

    # status.args 是启动参数字符串，如 "--port 8081 --device SYCL1 -c 8192 ..."
    args_str = ""
    status = loaded_detail.get("status")
    if isinstance(status, dict):
        args_val = status.get("args")
        if isinstance(args_val, str):
            args_str = args_val
        elif isinstance(args_val, list):
            args_str = " ".join(str(a) for a in args_val)
    # 也尝试顶层 args 字段
    if not args_str:
        args_val = loaded_detail.get("args")
        if isinstance(args_val, str):
            args_str = args_val
        elif isinstance(args_val, list):
            args_str = " ".join(str(a) for a in args_val)

    # 解析 --port
    m_port = re.search(r"--port\s+(\d+)", args_str)
    if m_port:
        info["port"] = int(m_port.group(1))

    # 解析 --device
    m_dev = re.search(r"--device\s+(\S+)", args_str)
    if m_dev:
        dev = m_dev.group(1)
    else:
        dev = "SYCL0"
    info["device"] = dev
    # 设备标签：优先用 --list-devices 解析的设备名（通用），查不到用原始值
    dev_label_map = _device_label_map()
    info["device_label"] = dev_label_map.get(dev, dev)

    # 通过端口查 PID
    if info["port"]:
        try:
            ps_out = subprocess.run(
                ["ps", "-eo", "pid,args"], capture_output=True, text=True, timeout=5
            ).stdout
            for line in ps_out.splitlines():
                if f"--port {info['port']}" in line and "llama-server" in line:
                    pid_str = line.strip().split()[0]
                    try:
                        info["pid"] = int(pid_str)
                    except ValueError:
                        pass
                    break
        except Exception:
            pass

    # loaded_at: 尝试从 status.created 或顶层字段取
    if isinstance(status, dict):
        created = status.get("created") or status.get("loaded_at")
        if created:
            info["loaded_at"] = created
    if not info["loaded_at"]:
        info["loaded_at"] = loaded_detail.get("created")

    return info


@router.get("")
def list_services():
    """列出模型池：DB 注册模型 + 目录扫描自动注册 + 实例状态

    架构（per-model 实例）：每个服务一个独立 llama-server 进程，
    状态从 instance_mgr 读取；目录扫描自动注册新模型。
    """
    from app import instance_mgr
    from app.routers.models import _scan_models

    # 目录扫描发现的模型（自动注册新模型）
    scanned = _scan_models()

    db_models = {}
    deleted_names = set()
    with get_conn() as conn:
        try:
            del_rows = conn.execute("SELECT name FROM deleted_models").fetchall()
            deleted_names = {r["name"] for r in del_rows}
        except Exception:
            pass
        rows = conn.execute("SELECT * FROM services ORDER BY id").fetchall()
        for r in rows:
            d = dict(r)
            d["args"] = json.loads(d["args"] or "{}")
            db_models[d["name"]] = d
        # 自动注册目录扫描发现的模型（排除 mmproj / hf-dir / 已删除墓碑）
        # name 用 _match_router_id 推导（子目录=目录名，根目录=文件名），
        # 与 create_service 一致，避免重复注册相对路径垃圾
        for sm in scanned:
            if sm.get("kind") != "gguf":
                continue
            if sm["name"].startswith("mmproj"):
                continue
            mid = _match_router_id(sm["path"]) or sm["name"]
            if mid.startswith("mmproj"):
                continue
            if mid in deleted_names:
                continue
            if mid not in db_models:
                cur = conn.execute(
                    "INSERT INTO services (name, model_path, args, status, created_at, updated_at) "
                    "VALUES (?,?, '{}', 'unloaded', ?, ?)",
                    (mid, sm["path"], now(), now()),
                )
                db_models[mid] = {
                    "id": cur.lastrowid, "name": mid, "model_path": sm["path"],
                    "args": {}, "status": "unloaded",
                    "created_at": now(), "updated_at": now(),
                }

    # 实例状态映射
    inst_map = instance_mgr.all_instances()

    result = []
    for mid, db_info in db_models.items():
        if mid.startswith("mmproj"):
            continue
        if mid in deleted_names:
            continue
        sid = db_info.get("id", 0)
        ist = inst_map.get(sid, {})
        # 内存态无记录/已死但端口被健康实例占用 → 孤儿实例（WebUI 重启/外部启动），探测并视为运行中
        if not ist or not ist.get("running"):
            p = instance_mgr._port_for(sid)
            if instance_mgr._port_in_use(p) and instance_mgr._health_ok(p):
                pid = instance_mgr._find_pid_on_port(p)
                ist = {"running": True, "state": "running", "port": p, "pid": pid,
                       "health_latency_ms": None, "last_health_at": None, "started_at": None}
                instance_mgr._instances[sid] = {"proc": None, "pid": pid, "port": p,
                                                "started_at": int(time.time()),
                                                "log_path": str(Path(settings.data_dir) / "instances" / f"{mid}.log")}
        running = bool(ist)
        port = ist.get("port")
        loaded_detail = {}
        if port:
            try:
                import httpx
                with httpx.Client(timeout=2) as c:
                    r = c.get(f"http://127.0.0.1:{port}/models")
                if r.status_code == 200:
                    data = r.json()
                    arr = data.get("data", []) if isinstance(data, dict) else data
                    if arr:
                        loaded_detail = arr[0]
                        # 字段归一化：per-model 实例 /models 返回 meta 嵌套结构 → 顶层字段
                        # （前端读 quant / mem_total / ctx_size）
                        meta = loaded_detail.get("meta") or {}
                        if isinstance(meta, dict):
                            if not loaded_detail.get("quant"):
                                loaded_detail["quant"] = meta.get("ftype")
                            if not loaded_detail.get("ctx_size"):
                                loaded_detail["ctx_size"] = meta.get("n_ctx")
                            if not loaded_detail.get("n_params") and meta.get("n_params"):
                                loaded_detail["n_params"] = meta.get("n_params")
            except Exception:
                pass
        # 显存占用：按实例 pid 查真实 RSS（MiB），比模型文件 size 更接近实际占用
        if running and ist.get("pid"):
            loaded_detail["mem_rss_mib"] = _proc_rss_mib(ist["pid"])
            loaded_detail["mem_total"] = (loaded_detail.get("mem_rss_mib") or 0) * 1024 * 1024
        has_mmproj = False
        mmproj_path = ""
        mp = db_info.get("model_path", "")
        if mp:
            from pathlib import Path as _P
            try:
                mm_dir = (_P(settings.model_dir) / mp.replace("/models/", "")).parent
                found = sorted(mm_dir.glob("mmproj*.gguf"))
                if found:
                    has_mmproj = True
                    mmproj_path = str(found[0])
            except Exception:
                pass
        result.append({
            "id": sid,
            "name": mid,
            "model_path": db_info.get("model_path") or "",
            "args": db_info.get("args", {}),
            "gpu_id": db_info.get("gpu_id", ""),
            "idle_unload_min": db_info.get("idle_unload_min", 0),
            "last_used_at": db_info.get("last_used_at", 0),
            "status": "loaded" if running else "unloaded",
            "loaded": running,
            "state": ist.get("state", "running" if running else "stopped"),
            "health_latency_ms": ist.get("health_latency_ms"),
            "last_health_at": ist.get("last_health_at"),
            "loaded_info": loaded_detail,
            "port": port,
            "device": None,
            "device_label": None,
            "pid": ist.get("pid"),
            "loaded_at": ist.get("started_at"),
            "supports_chat": _supports_chat(mid, loaded_detail),
            "has_mmproj": has_mmproj,
            "mmproj_path": mmproj_path,
            "created_at": db_info.get("created_at"),
            "updated_at": db_info.get("updated_at"),
        })

    return result


def _resolve_model_name(sid) -> dict:
    """将 DB id 解析为服务记录（name + model_path）"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "模型不存在，请先刷新模型列表")
    d = dict(row)
    d["router_id"] = _derive_router_id(d.get("model_path", ""))
    return d


def _proc_rss_mib(pid) -> int | None:
    """按 pid 查进程 RSS（MiB），失败返回 None"""
    if not pid:
        return None
    try:
        import subprocess as _sp
        out = _sp.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
        rss_kb = int(out.stdout.strip())
        return rss_kb // 1024
    except Exception:
        return None


def _preset_params(model_name: str) -> dict:
    """从 model_presets 表读取预设参数，转为 llama.cpp 启动参数 dict
    （kebab-case 键名）。查不到时返回空 dict。"""
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM model_presets WHERE model_name=?", (model_name,)
            ).fetchone()
    except Exception:
        return {}
    if not row:
        return {}
    d = dict(row)
    params = {}
    mapping = {
        "ctx_size": "ctx-size", "temp": "temperature", "threads": "threads",
        "batch_size": "batch-size", "ubatch_size": "ubatch-size", "parallel": "parallel",
        "cache_type_k": "cache-type-k", "cache_type_v": "cache-type-v",
        "n_gpu_layers": "n-gpu-layers",
    }
    for db_key, router_key in mapping.items():
        v = d.get(db_key)
        if v not in (None, ""):
            params[router_key] = v
    if d.get("flash_attn"):
        params["flash-attn"] = "on"
    if d.get("jinja"):
        params["jinja"] = "on"
    if not d.get("mmap", 1):
        params["no-mmap"] = "on"
    dev = d.get("device") or ""
    if dev and dev != "0":
        params["device"] = dev if dev.startswith("SYCL") or dev.startswith("CPU") else f"SYCL{dev}"
    try:
        extra = json.loads(d.get("extra_args") or "{}")
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
        for db_key, router_key in smap.items():
            v = sampling.get(db_key)
            if v in (None, ""):
                continue
            params[router_key] = v
    for k, v in extra.items():
        params[k] = v
    return params


def _derive_router_id(model_path: str) -> str:
    """从 model_path 推导模型 ID：
    - .gguf/.safetensors 文件 -> basename 去扩展名
    - 目录 -> 目录名
    如 /models/Qwen3.5-9B-Q6_K.gguf -> Qwen3.5-9B-Q6_K"""
    if not model_path:
        return ""
    from pathlib import Path as _P
    p = _P(model_path)
    if p.suffix in (".gguf", ".safetensors", ".bin"):
        return p.stem
    return p.name


def _match_router_id(model_path: str) -> str:
    """从 model_path 推导模型 ID（per-model 实例架构，不依赖 router）：
    - 文件名去扩展名；子目录模型取父目录名
    如 /models/Qwen3.5-9B-GGUF/Qwen3.5-9B-Q4_K_M.gguf -> Qwen3.5-9B-GGUF"""
    derived = _derive_router_id(model_path)
    if not derived:
        return derived
    from pathlib import Path as _P
    parent = _P(model_path).parent.name
    if parent and parent != "/" and parent != "models" and parent != derived:
        return parent
    return derived


@router.get("/router/status")
def router_status():
    """获取 router 健康状态和驻留模型详情"""
    healthy = router_client.health_check_sync()
    loaded = router_client.get_loaded_models_sync() if healthy else []
    return {
        "healthy": healthy,
        "router_url": settings.router_url,
        "loaded_models": loaded,
    }


@router.post("")
def create_service(body: ServiceCreate):
    """注册模型到模型池（仅 DB 记录，不加载）

    name 可选：不填或为空时自动推导为 router 模型 ID
    （_match_router_id：优先文件名，子目录模型用目录名），
    保证 name 与 router ID 一致，加载/聊天/预设全链路可用。
    """
    # 自动推导 name（优先级：显式传入 > router ID 匹配 > 文件名推导）
    name = (body.name or "").strip()
    if not name:
        name = _match_router_id(body.model_path) or _derive_router_id(body.model_path)
    with get_conn() as conn:
        dup = conn.execute("SELECT id FROM services WHERE name=?", (name,)).fetchone()
        if dup:
            raise HTTPException(400, f"模型 {name} 已注册")
        cur = conn.execute(
            "INSERT INTO services (name, model_path, args, gpu_id, idle_unload_min, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?, 'unloaded', ?, ?)",
            (name, body.model_path, json.dumps(body.args or {}), body.gpu_id or "",
             body.idle_unload_min or 0, now(), now()),
        )
        sid = cur.lastrowid
        # 主动重新注册：清除同名墓碑，避免 list_services 因墓碑跳过该模型
        # （墓碑本意是阻止“删除后文件仍在 /models 被自动注册”，
        #   用户手动重新注册时应视为解除删除，恢复正常展示）
        try:
            conn.execute("DELETE FROM deleted_models WHERE name=?", (name,))
            rid = _match_router_id(body.model_path)
            if rid and rid != name:
                conn.execute("DELETE FROM deleted_models WHERE name=?", (rid,))
        except Exception:
            pass
    # 校验：model_path 能否匹配到 router 模型 ID（避免注册后加载必 404）
    warning = None
    try:
        matched = _match_router_id(body.model_path)
        if not matched:
            warning = f"模型文件 {body.model_path} 无法匹配到 router 模型 ID，加载可能失败"
    except Exception:
        pass
    resp = {"id": sid, "name": name, "status": "unloaded"}
    if warning:
        resp["warning"] = warning
    return resp


@router.get("/{sid}")
def get_service(sid: int):
    """获取单个模型详情"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "模型不存在")
    d = dict(row)
    if d.get("name", "").startswith("mmproj"):
        raise HTTPException(404, "模型不存在")  # mmproj 投影文件不是模型
    d["args"] = json.loads(d["args"] or "{}")

    # 实例状态（per-model 独立进程）
    from app import instance_mgr
    ist = instance_mgr.instance_status(sid)
    running = ist.get("running", False)
    d["loaded"] = running
    d["status"] = "loaded" if running else "unloaded"
    d["state"] = ist.get("state", "running" if running else "stopped")
    d["health_latency_ms"] = ist.get("health_latency_ms")
    d["last_health_at"] = ist.get("last_health_at")
    d["port"] = ist.get("port")
    d["pid"] = ist.get("pid")
    d["loaded_at"] = ist.get("started_at")
    # 从实例 /models 拿 meta（若运行中）
    d["loaded_info"] = {}
    if running:
        try:
            import httpx
            with httpx.Client(timeout=2) as c:
                r = c.get(f"{instance_mgr.url_for(sid)}/models")
            if r.status_code == 200:
                data = r.json()
                arr = data.get("data", []) if isinstance(data, dict) else data
                if arr:
                    d["loaded_info"] = arr[0]
        except Exception:
            pass
    d["supports_chat"] = _supports_chat(d.get("name", ""), d.get("loaded_info") or {})

    # 检测 mmproj：模型 gguf 同目录是否有 mmproj*.gguf
    # （路径统一用 Path().parent，兼容根目录/子目录模型）
    d["has_mmproj"] = False
    d["mmproj_path"] = ""
    mp = d.get("model_path", "")
    if mp:
        from pathlib import Path as _P
        try:
            mm_dir = (_P(settings.model_dir) / mp.replace("/models/", "")).parent
            found = sorted(mm_dir.glob("mmproj*.gguf"))
            if found:
                d["has_mmproj"] = True
                d["mmproj_path"] = str(found[0])
        except Exception:
            pass

    return d


@router.put("/{sid}")
def update_service(sid: int, body: ServiceUpdate):
    """更新模型配置"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
        if not row:
            raise HTTPException(404, "模型不存在")
        d = dict(row)
        args = json.loads(d["args"] or "{}") if body.args is None else body.args
        model_path = d["model_path"] if body.model_path is None else body.model_path
        name = d["name"] if body.name is None else (body.name or "").strip()
        if not name:
            # 空 name：自动推导为 router ID
            name = _match_router_id(model_path) or _derive_router_id(model_path)
        gpu_id = d.get("gpu_id", "") if body.gpu_id is None else (body.gpu_id or "")
        idle_unload_min = d.get("idle_unload_min", 0) if body.idle_unload_min is None else (body.idle_unload_min or 0)
        conn.execute(
            "UPDATE services SET name=?, model_path=?, args=?, gpu_id=?, idle_unload_min=?, updated_at=? WHERE id=?",
            (name, model_path, json.dumps(args), gpu_id, idle_unload_min, now(), sid),
        )
    return {"ok": True}


@router.post("/{sid}/start")
def start_service(sid: int):
    """启动模型独立实例（per-model ctx，用预设的 ctx_size）

    异步语义：拉起进程后立即返回（不再同步等待健康），前端轮询
    instance_status / 日志实时推进进度。
    """
    from app import instance_mgr
    svc = _resolve_model_name(sid)
    name = svc["name"]
    model_path = svc.get("model_path", "")
    try:
        result = instance_mgr.start_instance(sid, name, model_path)
        # 复用已有实例：直接标记 loaded
        if result.get("status") == "running":
            with get_conn() as conn:
                conn.execute("UPDATE services SET status='loaded', updated_at=? WHERE name=?", (now(), name))
            return {
                "ok": True, "status": "loaded", "detail": result, "port": result.get("port"),
                "ready": True, "ready_hint": "模型已就绪（复用已有实例）",
            }
        # 新启动：立即返回，前端轮询状态/日志；DB 标记 loaded（自愈据此接管异常恢复）
        with get_conn() as conn:
            conn.execute("UPDATE services SET status='loaded', updated_at=? WHERE name=?", (now(), name))
        return {
            "ok": True, "status": "starting", "detail": result, "port": result.get("port"),
            "ready": False,
            "ready_hint": "模型启动中（异步），前端将轮询加载进度与日志",
        }
    except Exception as e:
        with get_conn() as conn:
            conn.execute("UPDATE services SET status='error', updated_at=? WHERE name=?", (now(), name))
        raise HTTPException(400, str(e))


@router.post("/{sid}/stop")
def stop_service(sid: int):
    """停止模型实例（终止独立进程）"""
    from app import instance_mgr
    svc = _resolve_model_name(sid)
    name = svc["name"]
    try:
        result = instance_mgr.stop_instance(sid)
        with get_conn() as conn:
            conn.execute("UPDATE services SET status='unloaded', updated_at=? WHERE name=?", (now(), name))
        return {"ok": True, "status": "unloaded", "detail": result}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/{sid}/restart")
def restart_service(sid: int):
    """重启模型：停止实例再启动（等待真正就绪）"""
    from app import instance_mgr
    svc = _resolve_model_name(sid)
    name = svc["name"]
    model_path = svc.get("model_path", "")
    # 先停止（失败不阻断）
    try:
        instance_mgr.stop_instance(sid)
        import time
        time.sleep(2)
    except Exception:
        pass
    # 启动
    try:
        result = instance_mgr.start_instance(sid, name, model_path)
        import time as _t
        import httpx
        ready = False
        base = instance_mgr.url_for(sid)
        for _ in range(50):
            _t.sleep(3)
            try:
                with httpx.Client(timeout=3) as c:
                    r = c.get(f"{base}/health")
                if r.status_code == 200:
                    ready = True
                    break
            except Exception:
                pass
        with get_conn() as conn:
            conn.execute("UPDATE services SET status='loaded', updated_at=? WHERE name=?", (now(), name))
        return {
            "ok": True, "status": "loaded", "detail": result, "port": result.get("port"),
            "ready": ready,
            "ready_hint": "模型已就绪" if ready else "模型仍在加载中（加载完成前对话请求会排队等待）",
        }
    except Exception as e:
        with get_conn() as conn:
            conn.execute("UPDATE services SET status='error', updated_at=? WHERE name=?", (now(), name))
        raise HTTPException(400, str(e))


@router.delete("/{sid}")
def delete_service(sid: int):
    """硬删除模型注册：物理删除 DB 记录 + 清理关联数据（预设/标签/聊天/统计），
    并写入墓碑表阻止 router 自动注册复活。模型文件保留。"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
        if not row:
            raise HTTPException(404, "模型不存在")
        d = dict(row)
        model_name = d["name"]
        router_id = _match_router_id(d.get("model_path", "")) or model_name

    # 若已加载，先停止实例进程（失败不阻断删除）
    try:
        from app import instance_mgr
        instance_mgr.stop_instance(sid)
    except Exception:
        pass

    with get_conn() as conn:
        # 物理删除 services 记录
        conn.execute("DELETE FROM services WHERE id=?", (sid,))
        # 写墓碑：阻止 router 自动注册复活（硬删除语义，持久化）
        # 记 router_id（自动注册按 router ID 匹配）；自定义 name 与 router_id 不同时都记
        for tname in {model_name, router_id}:
            if tname:
                conn.execute(
                    "INSERT OR IGNORE INTO deleted_models (name, created_at) VALUES (?,?)",
                    (tname, now()),
                )
        # 清理关联数据：预设 / 标签 / 聊天历史 / 会话 / 统计
        conn.execute("DELETE FROM model_presets WHERE model_name=?", (model_name,))
        conn.execute("DELETE FROM model_tags WHERE model_name=?", (model_name,))
        conn.execute("DELETE FROM chat_history WHERE sid=?", (sid,))
        conn.execute("DELETE FROM chat_sessions WHERE sid=?", (sid,))
        conn.execute("DELETE FROM api_stats WHERE model_name=?", (model_name,))
    return {"ok": True, "deleted": True, "tombstone": model_name}


@router.get("/{sid}/logs")
def service_logs(sid: int, tail: int = 200, since: Optional[str] = None, until: Optional[str] = None):
    """获取 llama-server 运行日志（读取 router.log 文件）"""
    from pathlib import Path
    from datetime import datetime, timezone
    import re

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "模型不存在")
    d = dict(row)

    # per-model 实例日志：instances/{name}.log
    from app import instance_mgr
    inst_log = Path(settings.data_dir) / "instances" / f"{d['name']}.log"
    log_file = inst_log if inst_log.exists() else Path(settings.data_dir) / "router.log"
    if not log_file.exists():
        return {"logs": "（日志文件不存在，模型未启动或未产生日志）", "total": 0, "file": str(log_file)}

    try:
        raw_lines = log_file.read_text(errors="replace").splitlines()
    except Exception as e:
        return {"logs": f"读取日志失败: {e}", "total": 0, "file": str(log_file)}

    total = len(raw_lines)

    # 时间过滤（如果提供了 since/until）
    # llama.cpp 日志格式示例: "[  1] 12.345.678 I srv ..."  （相对时间，难以解析）
    # 或带绝对时间的行（如果有）。策略：尝试匹配 ISO 时间戳前缀，否则按 mtime 粗略判断
    filtered = raw_lines

    if since or until:
        since_dt = None
        until_dt = None
        if since:
            try:
                since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            except ValueError:
                pass
        if until:
            try:
                until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
            except ValueError:
                pass

        # 尝试按行内时间戳过滤（匹配 HH:MM:SS 或 ISO 格式）
        ts_pattern = re.compile(r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})')
        time_only_pattern = re.compile(r'(\d{2}:\d{2}:\d{2})')

        if since_dt or until_dt:
            # 统一 since/until 时区为 UTC（naive -> UTC，避免与 aware 行时间比较崩溃）
            if since_dt and since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
            if until_dt and until_dt.tzinfo is None:
                until_dt = until_dt.replace(tzinfo=timezone.utc)

            timed_lines = []
            file_mtime = datetime.fromtimestamp(log_file.stat().st_mtime, tz=timezone.utc)
            for line in raw_lines:
                m = ts_pattern.search(line)
                if m:
                    try:
                        line_dt = datetime.fromisoformat(m.group(1))
                    except ValueError:
                        timed_lines.append(line)
                        continue
                else:
                    m2 = time_only_pattern.search(line)
                    if m2:
                        # 只有时间没有日期，用文件 mtime 的日期
                        try:
                            line_dt = datetime.fromisoformat(
                                file_mtime.strftime("%Y-%m-%d") + "T" + m2.group(1)
                            )
                        except ValueError:
                            timed_lines.append(line)
                            continue
                    else:
                        # 无时间戳行：保留（通常是多行日志的续行）
                        timed_lines.append(line)
                        continue

                # 统一时区比较
                if line_dt.tzinfo is None:
                    line_dt = line_dt.replace(tzinfo=timezone.utc)

                if since_dt and line_dt < since_dt:
                    continue
                if until_dt and line_dt > until_dt:
                    continue
                timed_lines.append(line)
            filtered = timed_lines

    # tail 截取
    if tail and tail > 0:
        result_lines = filtered[-tail:]
    else:
        result_lines = filtered

    logs_text = "\n".join(result_lines) if result_lines else "（无匹配日志）"

    return {
        "logs": logs_text,
        "total": len(result_lines),
        "file": str(log_file),
    }


@router.get("/params/schema")
def param_schema():
    """返回参数白名单（前端表单渲染用）"""
    from app.docker_mgr import PARAM_MAP, DEFAULT_ARGS
    return {
        "map": {k: {"flag": v[0], "type": (v[1].__name__ if hasattr(v[1], "__name__") else str(v[1]))} for k, v in PARAM_MAP.items()},
        "defaults": DEFAULT_ARGS,
    }


# ---------- 聊天代理（通过 /v1 统一网关，自动选模型） ----------

class ChatRequest(BaseModel):
    messages: list[dict]
    max_tokens: int = 1024
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    stream: bool = False
    chat_template_kwargs: Optional[dict] = None
    extra: Optional[dict] = None


def _normalize_messages(messages: list) -> list:
    """规范化 messages：末尾必须是 user（剔除末尾空 assistant/连续 assistant）
    防双请求/异常调用导致 llama.cpp 400 "Cannot have 2 or more assistant messages at the end"
    """
    msgs = [m for m in (messages or []) if isinstance(m, dict) and m.get("role")]
    while msgs:
        last = msgs[-1]
        if last.get("role") == "assistant" and not str(last.get("content", "") or "").strip():
            msgs.pop()  # 末尾空 assistant 剔除
        else:
            break
    # 合并末尾连续 assistant（保留最后一条非空）
    cleaned = []
    for m in msgs:
        if cleaned and m.get("role") == "assistant" and cleaned[-1].get("role") == "assistant":
            cleaned[-1] = m  # 保留后一条
        else:
            cleaned.append(m)
    return cleaned


@router.post("/{sid}/chat")
async def chat_proxy(sid: int, body: ChatRequest):
    """转发到该模型实例的 OpenAI 兼容端点（支持流式）"""
    import httpx
    from fastapi.responses import StreamingResponse

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "模型不存在")
    d = dict(row)
    # 用 router ID（与启停同一套匹配逻辑）作为请求 model 字段
    model_name = _match_router_id(d.get("model_path", "")) or d["name"]

    # per-model 实例路由
    from app import instance_mgr
    ist = instance_mgr.instance_status(sid)
    if not ist.get("running"):
        raise HTTPException(400, f"模型 {model_name} 未启动，请先启动模型")
    # M2 转发前探活：进程活着但 /health 不通（degraded）→ 明确 503 而非挂起
    if ist.get("state") == "degraded":
        lat = ist.get("health_latency_ms")
        raise HTTPException(503, f"模型 {model_name} 实例无响应（健康检查失败{('，延迟 ' + str(lat) + 'ms') if lat is not None else ''}），请重启模型")
    # M4 优雅停止：draining 期间拒绝新请求
    if not instance_mgr.begin_request(sid):
        raise HTTPException(503, f"模型 {model_name} 正在停止中（draining），请稍后重试")
    url = f"{instance_mgr.url_for(sid)}/v1/chat/completions"
    # 记录调用时间（空闲自动卸载）
    instance_mgr.touch_usage(sid)
    headers = {"Content-Type": "application/json"}

    payload = {
        "model": model_name,
        "messages": _normalize_messages(body.messages),
        "max_tokens": body.max_tokens,
        "stream": body.stream,
    }
    if body.temperature is not None:
        payload["temperature"] = body.temperature
    if body.top_p is not None:
        payload["top_p"] = body.top_p
    if body.chat_template_kwargs is not None:
        payload["chat_template_kwargs"] = body.chat_template_kwargs
    if body.extra:
        payload.update(body.extra)

    timeout = httpx.Timeout(600.0, connect=10.0)

    # 对话内容日志：创建 running 记录
    log_id = _chat_log_create(model_name, 1 if body.stream else 0, _last_user_message(body.messages))

    if not body.stream:
        import time as _time
        t0 = _time.time()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                try:
                    r = await client.post(url, json=payload, headers=headers)
                except httpx.HTTPError as e:
                    # 记录失败明细
                    _record_stats(model_name, stream=False, ok=False, status_code=502,
                                  total_ms=int((_time.time() - t0) * 1000), error=str(e))
                    _chat_log_finish(log_id, ok=False, status_code=502, total_ms=int((_time.time() - t0) * 1000), error=str(e))
                    raise HTTPException(502, f"转发失败: {e}")
                if r.status_code != 200:
                    _record_stats(model_name, stream=False, ok=False, status_code=r.status_code,
                                  total_ms=int((_time.time() - t0) * 1000),
                                  error=r.text[:300])
                    _chat_log_finish(log_id, ok=False, status_code=r.status_code,
                                     total_ms=int((_time.time() - t0) * 1000), error=r.text[:500])
                    raise HTTPException(r.status_code, f"上游返回 {r.status_code}: {r.text[:500]}")
                data = r.json()
                # 埋点统计
                elapsed_ms = int((_time.time() - t0) * 1000)
                usage = data.get("usage", {})
                _record_stats(model_name,
                              prompt_tokens=usage.get("prompt_tokens", 0),
                              completion_tokens=usage.get("completion_tokens", 0),
                              prefill_ms=elapsed_ms, stream=False, ok=True,
                              status_code=200, total_ms=elapsed_ms)
                # 对话内容日志：非流式直接拿完整内容
                try:
                    choice = data.get("choices", [{}])[0]
                    msg = choice.get("message", {})
                    resp_text = msg.get("content", "") or ""
                    think_text = msg.get("reasoning_content", "") or ""
                    _chat_log_append(log_id, resp_text[:20000], think_text[:20000])
                except Exception:
                    pass
                _chat_log_finish(log_id, ok=True, status_code=200,
                                 prompt_tokens=usage.get("prompt_tokens", 0),
                                 completion_tokens=usage.get("completion_tokens", 0),
                                 total_ms=elapsed_ms)
                return data
        finally:
            instance_mgr.end_request(sid)

    async def gen():
        import time as _time
        t0 = _time.time()
        first_token_time = None
        prompt_tokens = 0
        completion_tokens = 0
        # 对话内容日志：累积 response/thinking 片段 + 定期落库
        _resp_buf = []
        _think_buf = []
        _last_flush = _time.time()

        def _flush_chat(batch_resp: list, batch_think: list):
            nonlocal _last_flush
            now_t = _time.time()
            _chat_log_append(log_id, "".join(batch_resp), "".join(batch_think))
            batch_resp.clear()
            batch_think.clear()
            _last_flush = now_t

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                try:
                    async with client.stream("POST", url, json=payload, headers=headers) as r:
                        async for line in r.aiter_lines():
                            if line:
                                # 捕获首 token 时间
                                if first_token_time is None and line.startswith("data:") and "[DONE]" not in line:
                                    first_token_time = _time.time()
                                # 解析 usage（流式最后 chunk 可能有；llama.cpp 用 timings 字段）
                                if line.startswith("data:") and "[DONE]" not in line:
                                    try:
                                        chunk = json.loads(line[5:].strip())
                                        # 累积输出内容 + thinking
                                        delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                                        c = delta.get("content")
                                        t = delta.get("reasoning_content")
                                        if isinstance(c, str) and c:
                                            _resp_buf.append(c)
                                        if isinstance(t, str) and t:
                                            _think_buf.append(t)
                                        u = chunk.get("usage")
                                        if u:
                                            prompt_tokens = u.get("prompt_tokens", 0)
                                            completion_tokens = u.get("completion_tokens", 0)
                                        else:
                                            tt = chunk.get("timings")
                                            if tt:
                                                prompt_tokens = tt.get("prompt_n", 0)
                                                completion_tokens = tt.get("predicted_n", 0)
                                    except Exception:
                                        pass
                                    # 定期落库（每 1.5s 或 buffer 达 4KB）
                                    if _resp_buf or _think_buf:
                                        if _time.time() - _last_flush >= 1.5 or len("".join(_resp_buf)) >= 4096:
                                            _flush_chat(_resp_buf, _think_buf)
                                yield line + "\n"
                except httpx.HTTPError as e:
                    # 流式转发失败：记录失败明细（流式无法返回 HTTP 错误码，置 502）
                    total_ms = int((_time.time() - t0) * 1000)
                    _record_stats(model_name, stream=True, ok=False, status_code=502,
                                  total_ms=total_ms, error=str(e))
                    _flush_chat(_resp_buf, _think_buf)
                    _chat_log_finish(log_id, ok=False, status_code=502, total_ms=total_ms, error=str(e))
                    yield f"data: {{\"error\": \"{e}\"}}\n\n"
        finally:
            instance_mgr.end_request(sid)
        # 流式结束后埋点
        total_ms = int((_time.time() - t0) * 1000)
        prefill_ms = int((first_token_time - t0) * 1000) if first_token_time else total_ms
        decode_ms = max(0, total_ms - prefill_ms)
        _record_stats(model_name, prompt_tokens=prompt_tokens,
                      completion_tokens=completion_tokens,
                      prefill_ms=prefill_ms, decode_ms=decode_ms,
                      stream=True, ok=True, status_code=200, total_ms=total_ms)
        # 对话内容日志：收尾落库 + 完成
        _flush_chat(_resp_buf, _think_buf)
        _chat_log_finish(log_id, ok=True, status_code=200,
                         prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                         total_ms=total_ms)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------- 客户端配置导出 ----------

def _detect_lan_ip() -> str:
    """探测宿主机局域网 IP（供客户端接入配置使用）

    优先顺序：
    1. 环境变量 HOST_LAN_IP（部署脚本注入，最可靠）
    2. 环境变量 LLAMA_HOST_IP（兼容旧配置）
    3. socket 探测（容器内会拿到容器 IP，仅兜底）
    """
    import os as _os
    for env in ("HOST_LAN_IP", "LLAMA_HOST_IP"):
        v = (_os.environ.get(env) or "").strip()
        if v and v != "<HOST-IP>":
            return v
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "<HOST-IP>"


@router.get("/{sid}/client-config")
def client_config(sid: int):
    """生成 curl / openclaw / python 三种客户端配置片段"""
    import socket

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "模型不存在")
    d = dict(row)
    model_name = d["name"]

    host_ip = _detect_lan_ip()

    base = f"http://{host_ip}:{settings.webui_port}/v1"
    # 所有启用的 key（多 token 时前端可选择展示）
    with get_conn() as conn:
        key_rows = conn.execute(
            "SELECT id, name, key FROM api_keys WHERE enabled=1 ORDER BY id"
        ).fetchall()
    keys = [dict(r) for r in key_rows] if key_rows else []
    key = keys[0]["key"] if keys else "<在系统设置生成 API 密钥>"
    auth = f'"Authorization: Bearer {key}"' if keys else ""
    auth_line = f'  -H {auth} \\' if auth else ""

    curl = f'''curl {base}/chat/completions \\
  -H "Content-Type: application/json" \\
{auth_line}
  -d '{{"model": "{model_name}", "messages": [{{"role": "user", "content": "你好"}}], "max_tokens": 100}}'
'''

    openclaw = f'''# openclaw.json models.providers 片段
"llm-studio": {{
  "type": "openai",
  "baseUrl": "{base}",
  "apiKey": "{key}",
  "models": ["{model_name}"]
}}'''

    python = f'''import openai

client = openai.OpenAI(
    base_url="{base}",
    api_key="{key}",
)
resp = client.chat.completions.create(
    model="{model_name}",
    messages=[{{"role": "user", "content": "你好"}}],
)
print(resp.choices[0].message.content)'''

    return {
        "base_url": base,
        "model": model_name,
        "curl": curl.strip(),
        "openclaw": openclaw.strip(),
        "python": python.strip(),
        "keys": keys,
        "active_key": key,
    }


# ---------- 聊天历史 ----------

class HistoryItem(BaseModel):
    role: str
    content: str = ""
    thinking: str = ""
    session_id: int = 0


class SessionCreate(BaseModel):
    title: str = ""


class SessionRename(BaseModel):
    title: str


@router.get("/{sid}/sessions")
def list_sessions(sid: int):
    """列出服务的所有会话（含消息数、更新时间）"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM chat_history h WHERE h.session_id = s.id AND h.sid = s.sid) as msg_count "
            "FROM chat_sessions s WHERE s.sid=? ORDER BY s.updated_at DESC",
            (sid,),
        ).fetchall()
        # 也加入默认会话(session_id=0)的消息数
        default_count = conn.execute(
            "SELECT COUNT(*) as c FROM chat_history WHERE sid=? AND (session_id=0 OR session_id IS NULL)", (sid,)
        ).fetchone()["c"]
    result = [dict(r) for r in rows]
    # 默认会话始终存在
    result.append({"id": 0, "sid": sid, "title": "默认会话", "created_at": 0, "updated_at": 0, "msg_count": default_count})
    return result


@router.post("/{sid}/sessions")
def create_session(sid: int, body: SessionCreate):
    """新建会话"""
    title = body.title or f"新会话"
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO chat_sessions (sid, title, created_at, updated_at) VALUES (?,?,?,?)",
            (sid, title, now(), now()),
        )
        session_id = cur.lastrowid
    return {"id": session_id, "sid": sid, "title": title}


@router.patch("/{sid}/sessions/{session_id}")
def rename_session(sid: int, session_id: int, body: SessionRename):
    """重命名会话"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE chat_sessions SET title=?, updated_at=? WHERE id=? AND sid=?",
            (body.title, now(), session_id, sid),
        )
    return {"ok": True}


@router.delete("/{sid}/sessions/{session_id}")
def delete_session(sid: int, session_id: int):
    """删除会话（连带历史）"""
    with get_conn() as conn:
        conn.execute("DELETE FROM chat_history WHERE sid=? AND session_id=?", (sid, session_id))
        conn.execute("DELETE FROM chat_sessions WHERE id=? AND sid=?", (session_id, sid))
    return {"ok": True}


@router.get("/{sid}/history")
def get_history(sid: int, session_id: int = 0):
    """获取聊天历史（按会话隔离）"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, role, content, thinking, created_at FROM chat_history "
            "WHERE sid=? AND (session_id=? OR (session_id IS NULL AND ?=0)) ORDER BY id",
            (sid, session_id, session_id),
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/{sid}/history")
def add_history(sid: int, body: HistoryItem):
    """追加聊天历史"""
    sess = body.session_id or 0
    with get_conn() as conn:
        last = conn.execute(
            "SELECT role, content FROM chat_history WHERE sid=? AND (session_id=? OR (session_id IS NULL AND ?=0)) ORDER BY id DESC LIMIT 1",
            (sid, sess, sess),
        ).fetchone()
        if body.role == 'user' and last and last['role'] == 'user' and last['content'] == body.content:
            return {"ok": True, "skipped": "duplicate"}
        cur = conn.execute(
            "INSERT INTO chat_history (sid, session_id, role, content, thinking, created_at) VALUES (?,?,?,?,?,?)",
            (sid, sess, body.role, body.content, body.thinking, now()),
        )
        hid = cur.lastrowid
        # 更新会话 updated_at
        if sess > 0:
            conn.execute("UPDATE chat_sessions SET updated_at=? WHERE id=? AND sid=?", (now(), sess, sid))
    return {"ok": True, "id": hid}


@router.delete("/{sid}/history")
def clear_history(sid: int, session_id: int = 0):
    """清空指定会话的聊天历史"""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM chat_history WHERE sid=? AND (session_id=? OR (session_id IS NULL AND ?=0))",
            (sid, session_id, session_id),
        )
    return {"ok": True}


@router.delete("/{sid}/history/{history_id}")
def delete_history_item(sid: int, history_id: int):
    """删除单条聊天历史"""
    with get_conn() as conn:
        conn.execute("DELETE FROM chat_history WHERE id=? AND sid=?", (history_id, sid))
    return {"ok": True}


# ---------- PDF 解析 ----------

@router.post("/{sid}/parse-pdf")
async def parse_pdf(sid: int, file: UploadFile):
    """上传 PDF 文件，返回提取的文本"""
    from fastapi import UploadFile as _UF
    import io

    content = await file.read()
    if not content:
        raise HTTPException(400, "空文件")

    text = ""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=content, filetype="pdf")
        for page in doc:
            text += page.get_text()
        doc.close()
    except ImportError:
        # PyMuPDF 不可用，尝试 pdfplumber
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                for page in pdf.pages:
                    text += page.extract_text() or ""
        except ImportError:
            raise HTTPException(500, "PDF 解析库未安装（需要 PyMuPDF 或 pdfplumber）")

    if not text.strip():
        text = "（PDF 未提取到文本，可能是扫描件）"

    return {"text": text[:8000]}  # 截断防止超长


@router.get("/gateway/health")
def gateway_health():
    """网关心跳聚合：所有服务实例状态 + 健康数据（M6）"""
    import time as _t
    from app import instance_mgr
    try:
        svcs = list_services()
        instances = []
        for s in svcs:
            # 对运行中实例补一次健康探测（TTL 缓存内复用，无额外开销）
            health = {}
            if s.get("loaded") and s.get("id"):
                try:
                    health = instance_mgr.instance_status(s["id"])
                except Exception:
                    pass
            instances.append({
                "id": s.get("id"),
                "name": s.get("name"),
                "state": s.get("state", "stopped"),
                "loaded": s.get("loaded", False),
                "pid": s.get("pid"),
                "port": s.get("port"),
                "health_latency_ms": health.get("health_latency_ms") or s.get("health_latency_ms"),
                "last_health_at": health.get("last_health_at") or s.get("last_health_at"),
                "mem_mib": (s.get("loaded_info") or {}).get("mem_rss_mib"),
                "quant": (s.get("loaded_info") or {}).get("quant"),
                "ctx_size": (s.get("loaded_info") or {}).get("ctx_size"),
            })
        running = sum(1 for i in instances if i["state"] == "running")
        degraded = sum(1 for i in instances if i["state"] == "degraded")
        starting = sum(1 for i in instances if i["state"] == "starting")
        return {
            "ok": True,
            "generated_at": int(_t.time()),
            "total": len(instances),
            "running": running,
            "degraded": degraded,
            "starting": starting,
            "instances": instances,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "instances": []}
