"""Docker 容器管理 - 已废弃，保留空壳用于兼容旧 import"""
# 此文件已废弃，功能迁移到 router_client.py
# 仅为兼容旧代码的 import 不报错而保留


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


def detect_gpus() -> list[dict]:
    """检测可用显卡（容器内简化版）"""
    import glob
    import os
    gpus = []
    for card in sorted(glob.glob("/dev/dri/card*")):
        idx = card.split("card")[-1]
        render = f"/dev/dri/renderD{128 + int(idx)}"
        gpus.append({
            "id": f"card{idx}",
            "name": f"GPU card{idx}",
            "card": card,
            "render": render,
            "devices": [card, render],
        })
    return gpus


def sync_status():
    """兼容旧接口 - 空操作"""
    pass


def list_services() -> list[dict]:
    """兼容旧接口 - 返回空列表"""
    return []


def get_service(sid: int):
    return None


def get_container_logs(sid: int, tail: int = 200) -> str:
    return "(日志功能已迁移到 router 模式)"
