"""Docker 容器管理 - 封装 llama-server SYCL 容器的完整生命周期"""
import json
import socket
import time
from typing import Optional

import docker
from docker.errors import NotFound, APIError

from app.config import settings
from app.database import get_conn, now

# 关键参数白名单（图形化表单可设置的参数 -> llama-server CLI 参数）
PARAM_MAP = {
    "n_gpu_layers": ("-ngl", int),
    "ctx_size": ("-c", int),
    "batch_size": ("-b", int),
    "ubatch_size": ("--ubatch-size", int),
    "parallel": ("-np", int),
    "flash_attn": ("--flash-attn", "flash"),
    "cache_type_k": ("--cache-type-k", str),
    "cache_type_v": ("--cache-type-v", str),
    "jinja": ("--jinja", bool),
    "no_webui": ("--no-webui", bool),
    "temp": ("--temp", float),
    "top_k": ("--top-k", int),
    "top_p": ("--top-p", float),
    "repeat_penalty": ("--repeat-penalty", float),
    "threads": ("-t", int),
    "verbose": ("-v", bool),
}

# 默认参数（9B 模型推荐配置）
DEFAULT_ARGS = {
    "n_gpu_layers": 99,
    "ctx_size": 32768,
    "batch_size": 2048,
    "ubatch_size": 512,
    "parallel": 4,
    "flash_attn": True,
    "cache_type_k": "q8_0",
    "cache_type_v": "q8_0",
    "jinja": True,
    "no_webui": True,
}


def _client() -> docker.DockerClient:
    """连接宿主 docker（容器内则走挂载的 socket）"""
    try:
        return docker.from_env()
    except Exception:
        # 兜底：显式指定 socket
        return docker.DockerClient(base_url="unix://var/run/docker.sock")


def find_free_port(preferred: Optional[int] = None) -> int:
    """找空闲端口（避开已占用）"""
    used = set()
    with get_conn() as conn:
        for row in conn.execute("SELECT port FROM services"):
            used.add(row["port"])
    if preferred and preferred not in used:
        return preferred
    for port in range(settings.port_min, settings.port_max + 1):
        if port in used:
            continue
        with socket.socket() as s:
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise RuntimeError("无可用端口")


def build_container_args(model_path: str, port: int, args: dict, api_key: Optional[str] = None) -> list[str]:
    """把表单参数转为 llama-server 命令行参数"""
    cmd = [
        "-m", model_path,
        "--port", str(port),
        "--host", "0.0.0.0",
    ]
    for key, (flag, caster) in PARAM_MAP.items():
        if key not in args:
            continue
        val = args[key]
        if caster is bool:
            if val:
                cmd.append(flag)
        elif caster == "flash":
            if val:
                cmd += [flag, "on"]
        else:
            cmd.append(flag)
            cmd.append(str(caster(val)))
    if api_key:
        cmd += ["--api-key", api_key]
    return cmd


def create_service(name: str, model_path: str, args: dict, api_key: Optional[str] = None,
                   port: Optional[int] = None) -> dict:
    """创建服务（注册到 DB，不启动容器）"""
    port = port or find_free_port()
    client = _client()
    try:
        client.images.get(settings.llama_image)
    except NotFound:
        raise RuntimeError(f"镜像 {settings.llama_image} 不存在，请先拉取")

    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO services (name, model_path, port, args, api_key, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?, 'stopped', ?, ?)",
            (name, model_path, port, json.dumps(args), api_key, now(), now()),
        )
        sid = cur.lastrowid
    return get_service(sid)


def start_service(sid: int) -> dict:
    """启动服务（创建并运行容器）"""
    svc = get_service(sid)
    if not svc:
        raise RuntimeError("服务不存在")
    client = _client()
    container_name = f"llm-{svc['name']}"

    # 清理同名的旧容器
    try:
        old = client.containers.get(container_name)
        old.remove(force=True)
    except NotFound:
        pass

    cmd = build_container_args(svc["model_path"], svc["port"], svc["args"], svc["api_key"])

    # 挂载模型目录 + A770M 设备
    volumes = {settings.model_dir: {"bind": "/models", "mode": "ro"}}
    devices = []
    for d in settings.gpu_devices:
        devices.append(f"{d}:{d}")

    env = {
        "ZES_ENABLE_SYSMAN": "1",
        "GGML_SYCL_ENABLE_FLASH_ATTN": "1",
    }

    try:
        container = client.containers.run(
            image=settings.llama_image,
            name=container_name,
            command=cmd,
            detach=True,
            volumes=volumes,
            devices=devices,
            environment=env,
            ports={f"{svc['port']}/tcp": svc["port"]},
            entrypoint=None,
            restart_policy={"Name": "unless-stopped"},
            healthcheck={
                "test": ["CMD", "python3", "-c",
                          "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%d/health', timeout=5).status==200 else 1)" % svc["port"]],
                "interval": 10000000000,
                "timeout": 8000000000,
                "retries": 6,
                "start_period": 120000000000,
            },
        )
        with get_conn() as conn:
            conn.execute(
                "UPDATE services SET status='running', container_id=?, updated_at=? WHERE id=?",
                (container.id, now(), sid),
            )
    except APIError as e:
        with get_conn() as conn:
            conn.execute("UPDATE services SET status='error', updated_at=? WHERE id=?", (now(), sid))
        raise RuntimeError(f"容器启动失败: {e}")

    return get_service(sid)


def stop_service(sid: int) -> dict:
    """停止服务"""
    svc = get_service(sid)
    if not svc:
        raise RuntimeError("服务不存在")
    client = _client()
    try:
        container = client.containers.get(f"llm-{svc['name']}")
        container.stop(timeout=10)
        container.remove()
    except NotFound:
        pass
    with get_conn() as conn:
        conn.execute(
            "UPDATE services SET status='stopped', container_id=NULL, updated_at=? WHERE id=?",
            (now(), sid),
        )
    return get_service(sid)


def restart_service(sid: int) -> dict:
    stop_service(sid)
    return start_service(sid)


def get_service(sid: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if not row:
        return None
    svc = dict(row)
    svc["args"] = json.loads(svc["args"])
    return svc


def list_services() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM services ORDER BY id").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["args"] = json.loads(d["args"])
        out.append(d)
    return out


def delete_service(sid: int):
    svc = get_service(sid)
    if not svc:
        return
    if svc["status"] == "running":
        stop_service(sid)
    with get_conn() as conn:
        conn.execute("DELETE FROM services WHERE id=?", (sid,))


def get_container_logs(sid: int, tail: int = 200) -> str:
    """获取容器日志"""
    svc = get_service(sid)
    if not svc:
        return ""
    client = _client()
    try:
        container = client.containers.get(f"llm-{svc['name']}")
        return container.logs(tail=tail).decode("utf-8", errors="replace")
    except NotFound:
        return "(容器不存在)"


def sync_status():
    """同步所有服务的真实状态（容器是否在跑）"""
    client = _client()
    running = set()
    for c in client.containers.list(all=True):
        if c.name.startswith("llm-"):
            running.add(c.name[4:])
    with get_conn() as conn:
        for r in conn.execute("SELECT id, name, status FROM services").fetchall():
            real = "running" if r["name"] in running else "stopped"
            if real != r["status"]:
                conn.execute(
                    "UPDATE services SET status=?, updated_at=? WHERE id=?",
                    (real, now(), r["id"]),
                )
