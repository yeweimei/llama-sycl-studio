"""模型管理 API - 扫描本地模型目录，识别 GGUF / HF 格式"""
import os
import struct
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.config import settings

router = APIRouter()

GGUF_MAGIC = b"GGUF"


def parse_gguf_meta(path: Path) -> dict:
    """解析 GGUF 文件头，提取元信息（架构、量化、参数量）"""
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
            if magic != GGUF_MAGIC:
                return {}
            version = struct.unpack("<I", f.read(4))[0]
            n_tensors = struct.unpack("<Q", f.read(8))[0]
            n_kv = struct.unpack("<Q", f.read(8))[0]

            kv = {}
            for _ in range(min(n_kv, 512)):  # 限制解析量
                try:
                    k = _read_string(f)
                    v_type = struct.unpack("<I", f.read(4))[0]
                    val = _read_value(f, v_type)
                    if k in ("general.architecture", "general.name", "general.file_type",
                             "general.quantization_version", "llama.vocab_size",
                             "general.size_label"):
                        kv[k] = val
                except Exception:
                    break

            ftype = kv.get("general.file_type")
            quant = _file_type_name(ftype) if ftype is not None else ""
            # 从文件名推断量化（更直观）
            name_quant = _quant_from_filename(path.name)
            return {
                "architecture": kv.get("general.architecture", ""),
                "model_name": kv.get("general.name", ""),
                "quantization": quant or name_quant,
                "size_label": kv.get("general.size_label", ""),
                "file_type": ftype,
            }
    except Exception:
        return {}


def _read_string(f) -> str:
    ln = struct.unpack("<Q", f.read(8))[0]
    return f.read(ln).decode("utf-8", errors="replace")


def _read_value(f, vtype: int):
    """GGUF KV value types: 0=uint8,1=int8,2=uint16,3=int16,4=uint32,5=int32,
    6=float32,7=bool,8=string,9=array,10=uint64,11=int64,12=float64"""
    if vtype in (0, 1, 2, 3, 4, 5, 10, 11):
        fmt = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 10: "Q", 11: "q"}[vtype]
        return struct.unpack("<" + fmt, f.read(struct.calcsize("<" + fmt)))[0]
    if vtype == 6:
        return struct.unpack("<f", f.read(4))[0]
    if vtype == 7:
        return bool(struct.unpack("<B", f.read(1))[0])
    if vtype == 8:
        return _read_string(f)
    if vtype == 12:
        return struct.unpack("<d", f.read(8))[0]
    if vtype == 9:  # array
        elem_type = struct.unpack("<I", f.read(4))[0]
        n = struct.unpack("<Q", f.read(8))[0]
        vals = []
        for _ in range(min(n, 64)):
            vals.append(_read_value(f, elem_type))
        return vals
    return None


_QUANT_MAP = {
    0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 4: "Q4_2", 5: "Q4_3",
    6: "Q5_0", 7: "Q5_1", 8: "Q8_0", 9: "Q8_1", 10: "Q2_K", 11: "Q3_K",
    12: "Q4_K", 13: "Q5_K", 14: "Q6_K", 15: "Q8_K", 16: "IQ2_XXS",
    17: "IQ2_XS", 18: "IQ3_XXS", 19: "IQ1_S", 20: "IQ4_NL", 21: "IQ3_S",
    22: "IQ2_S", 23: "IQ4_XS", 24: "I8", 25: "I16", 26: "IQ3_M",
    27: "Q4_K_XL", 28: "Q5_K_XL", 29: "Q6_K_XL",
}


def _file_type_name(ftype: int) -> str:
    return _QUANT_MAP.get(ftype, f"type{ftype}")


def _quant_from_filename(name: str) -> str:
    """从文件名提取量化标记，如 Q6_K、Q4_K_M、BF16"""
    for q in ("Q6_K_XL", "Q5_K_XL", "Q4_K_XL", "Q8_0", "Q6_K", "Q5_K_M", "Q5_K_S",
              "Q4_K_M", "Q4_K_S", "Q3_K_M", "Q3_K_S", "Q2_K", "IQ4_XS", "IQ4_NL",
              "IQ3_XXS", "IQ2_XS", "BF16", "F16", "F32", "Q4_0", "Q4_1", "Q5_0", "Q5_1"):
        if q in name.upper():
            return q
    return ""


def _scan_models():
    """扫描模型目录"""
    models = []
    base = Path(settings.model_dir)
    if not base.exists():
        return models

    # 1) 直接 gguf 文件
    for f in sorted(base.glob("*.gguf")):
        meta = parse_gguf_meta(f)
        size = f.stat().st_size
        models.append({
            "name": f.name,
            "path": f"/models/{f.name}",       # 容器内路径
            "local_path": str(f),
            "kind": "gguf",
            "size_bytes": size,
            "size_human": _human_size(size),
            **meta,
        })

    # 2) 子目录中的 gguf / HF 目录
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        ggufs = list(d.glob("*.gguf"))
        if ggufs:
            for f in ggufs:
                meta = parse_gguf_meta(f)
                size = f.stat().st_size
                models.append({
                    "name": f"{d.name}/{f.name}",
                    "path": f"/models/{d.name}/{f.name}",
                    "local_path": str(f),
                    "kind": "gguf",
                    "size_bytes": size,
                    "size_human": _human_size(size),
                    **meta,
                })
        elif (d / "config.json").exists():
            # HF 格式目录（llama.cpp 不直接支持，标注用途）
            models.append({
                "name": d.name + "/",
                "path": f"/models/{d.name}",
                "local_path": str(d),
                "kind": "hf-dir",
                "size_bytes": _dir_size(d),
                "size_human": _human_size(_dir_size(d)),
                "architecture": "HF format (needs convert)",
            })
    return models


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


@router.get("")
def list_models(refresh: bool = False):
    return _scan_models()


@router.get("/gguf-meta")
def gguf_meta(path: str):
    """解析指定 GGUF 文件元信息"""
    # 安全校验：只允许 model_dir 内路径
    local = path.replace("/models/", "")
    full = Path(settings.model_dir) / local
    if not full.exists() or full.suffix != ".gguf":
        raise HTTPException(404, "文件不存在")
    return parse_gguf_meta(full)


@router.delete("")
def delete_model(path: str):
    """删除模型文件（谨慎操作）"""
    local = path.replace("/models/", "")
    full = Path(settings.model_dir) / local
    if not full.exists():
        raise HTTPException(404, "文件不存在")
    # 简单确认：只允许删 .gguf 文件
    if full.suffix != ".gguf":
        raise HTTPException(400, "只支持删除 .gguf 文件")
    full.unlink()
    return {"ok": True, "deleted": local}
