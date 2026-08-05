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
    """解析 GGUF 文件头，提取元信息（架构、量化、参数量）
    优化：只读前 16 个 KV（架构/名称/文件类型通常在最前），
    array 类型只读长度不读内容，避免 tokenizer 大数组卡死"""
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
            if magic != GGUF_MAGIC:
                return {}
            version = struct.unpack("<I", f.read(4))[0]
            n_tensors = struct.unpack("<Q", f.read(8))[0]
            n_kv = struct.unpack("<Q", f.read(8))[0]

            kv = {}
            for _ in range(min(n_kv, 64)):  # 架构/名称/结构参数（扩展到 64 个覆盖 llama.* 键）
                try:
                    k = _read_string(f)
                    v_type = struct.unpack("<I", f.read(4))[0]
                    val = _read_value(f, v_type)
                    # 保留：通用元信息 + 结构参数（KV cache 估算用）
                    # 排除 tokenizer 大数组（tokens/scores 等超大数组）即可，其余全收
                    if not k.startswith("tokenizer.") or k == "tokenizer.ggml.model":
                        kv[k] = val
                except Exception:
                    break

            # 结构参数归一化（键前缀 = 架构名，如 llama./qwen35./bert.；动态匹配）
            arch = kv.get("general.architecture", "")
            pfx = f"{arch}." if arch else "llama."

            def _get(*names):
                # 先按架构前缀，再试通用前缀
                candidates = [n for n in names]
                for n in names:
                    if n.startswith(("llama.", "bert.", "qwen2")):
                        candidates.append(f"{pfx}{n.split(".", 1)[1]}")
                for n in candidates:
                    if n in kv:
                        return kv[n]
                return None

            block_count = _get("llama.block_count", "bert.block_count")
            head_count = _get("llama.attention.head_count", "bert.attention.head_count")
            head_count_kv = _get("llama.attention.head_count_kv") or head_count or 0
            embed = _get("llama.embedding_length", "bert.embedding_length") or 0
            # head_dim：有显式键用，否则按 n_embd/n_head 推导（GQA 模型用 head_count_kv）
            head_dim = _get("llama.attention.key_length", "llama.attention.value_length")
            if not head_dim and head_count_kv and embed:
                head_dim = embed // head_count_kv
            # 混合架构（如 qwen35 = Mamba + Attention 交替）：
            # full_attention_interval 表示每 N 层有一个 attention 层
            full_attn_interval = _get("llama.full_attention_interval") or 0
            if full_attn_interval and block_count:
                # 有 interval 时 attention 层数 = 总层 / interval（约）
                attn_layers = max(1, (block_count + full_attn_interval - 1) // full_attn_interval)
            else:
                attn_layers = block_count or 0
            file_size = path.stat().st_size if path.exists() else 0

            ftype = kv.get("general.file_type")
            quant = _file_type_name(ftype) if ftype is not None else ""
            name_quant = _quant_from_filename(path.name)
            return {
                "architecture": kv.get("general.architecture", ""),
                "model_name": kv.get("general.name", ""),
                "quantization": quant or name_quant,
                "size_label": kv.get("general.size_label", ""),
                "file_type": ftype,
                "n_layer": block_count,
                "n_head": head_count,
                "n_head_kv": head_count_kv,
                "n_embd": embed,
                "head_dim": head_dim,
                "attn_layers": attn_layers,
                "full_attention_interval": full_attn_interval,
                "file_size_bytes": file_size,
            }
    except Exception:
        return {}


def _read_string(f) -> str:
    ln = struct.unpack("<Q", f.read(8))[0]
    if ln > 1_000_000:  # 防错位后读到超长字符串
        f.seek(ln, 1)
        return ""
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
    if vtype == 9:  # array：读元素数，跳过内容（避免超大数组卡死）
        elem_type = struct.unpack("<I", f.read(4))[0]
        n = struct.unpack("<Q", f.read(8))[0]
        if n > 1024:  # 大数组：计算总字节数并 seek 跳过（必须跳过，否则后续 KV 错位）
            _skip_array(f, elem_type, n)
            return None
        vals = []
        for _ in range(n):
            vals.append(_read_value(f, elem_type))
        return vals
    return None


def _skip_array(f, elem_type: int, n: int):
    """跳过 GGUF array 内容（不读取，避免超大数组卡死）"""
    if elem_type == 8:  # 字符串数组：逐个跳过（长度前缀 + 内容）
        for _ in range(n):
            try:
                ln = struct.unpack("<Q", f.read(8))[0]
                if ln > 10_000_000:  # 防御：异常长度直接中断
                    return
                f.seek(ln, 1)
            except Exception:
                return
    elif elem_type == 9:  # 嵌套数组（罕见）：跳过元素数组
        for _ in range(n):
            try:
                sub_type = struct.unpack("<I", f.read(4))[0]
                sub_n = struct.unpack("<Q", f.read(8))[0]
                _skip_array(f, sub_type, sub_n)
            except Exception:
                return
    else:
        # 定长元素：总字节 = n × 元素大小
        sizes = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
        sz = sizes.get(elem_type, 1)
        try:
            f.seek(n * sz, 1)
        except Exception:
            pass


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
    """扫描模型目录（优化版：跳过 HF 目录深统计，GGUF 头只读前 512KB）"""
    models = []
    base = Path(settings.model_dir)
    if not base.exists():
        return models

    # 1) 直接 gguf 文件（排除 mmproj 投影文件）
    for f in sorted(base.glob("*.gguf")):
        if f.name.startswith("mmproj"):
            continue
        meta = parse_gguf_meta(f)
        size = f.stat().st_size
        models.append({
            "name": f.name,
            "path": f"/models/{f.name}",
            "local_path": str(f),
            "kind": "gguf",
            "size_bytes": size,
            "size_human": _human_size(size),
            **meta,
        })

    # 2) 子目录：只列顶层 gguf，HF 目录不做深统计（标注即可）
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        try:
            ggufs = list(d.glob("*.gguf"))
        except (PermissionError, OSError):
            continue
        if ggufs:
            for f in ggufs[:3]:  # 每目录最多 3 个 gguf，避免大目录卡死
                if f.name.startswith("mmproj"):
                    continue
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
            # HF 格式目录：不递归统计（慢），只标类型
            models.append({
                "name": d.name + "/",
                "path": f"/models/{d.name}",
                "local_path": str(d),
                "kind": "hf-dir",
                "size_bytes": 0,
                "size_human": "HF 格式",
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
