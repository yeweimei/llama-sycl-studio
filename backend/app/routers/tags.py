"""模型标签 API - 自动打标 + 自定义标签"""
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.database import get_conn, now

router = APIRouter()

# 预置标签关键词映射
_TAG_RULES = [
    ("思考", ["think", "reasoning", "reasoner"]),
    ("多模态", ["vl", "vlm", "vision", "visual", "ocr"]),
    ("MoE", ["moe", "a3b"]),
    ("Embedding", ["embed", "embedding"]),
    ("OCR", ["ocr"]),
    ("TTS", ["tts", "speech", "voice"]),
    ("Dense", ["dense"]),
]


def _auto_tags(model_name: str) -> list[str]:
    """根据模型名关键词自动打标"""
    name_lower = model_name.lower()
    tags = []
    for tag, keywords in _TAG_RULES:
        if any(kw in name_lower for kw in keywords):
            if tag not in tags:
                tags.append(tag)
    return tags


class TagUpdate(BaseModel):
    tags: list[str] = []
    custom_tags: list[str] = []


@router.get("")
def list_all_tags():
    """获取所有模型标签"""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM model_tags ORDER BY model_name").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["tags"] = json.loads(d["tags"] or "[]")
        d["custom_tags"] = json.loads(d["custom_tags"] or "[]")
        out.append(d)
    return out


@router.get("/{model_name}")
def get_tags(model_name: str):
    """获取单个模型标签（不存在则返回自动打标结果）"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM model_tags WHERE model_name=?", (model_name,)).fetchone()
    if row:
        d = dict(row)
        d["tags"] = json.loads(d["tags"] or "[]")
        d["custom_tags"] = json.loads(d["custom_tags"] or "[]")
        return d
    # 自动打标
    return {"model_name": model_name, "tags": _auto_tags(model_name), "custom_tags": []}


@router.put("/{model_name}")
def update_tags(model_name: str, body: TagUpdate):
    """更新模型标签（upsert）"""
    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM model_tags WHERE model_name=?", (model_name,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE model_tags SET tags=?, custom_tags=?, updated_at=? WHERE model_name=?",
                (json.dumps(body.tags), json.dumps(body.custom_tags), now(), model_name),
            )
        else:
            conn.execute(
                "INSERT INTO model_tags (model_name, tags, custom_tags, updated_at) VALUES (?,?,?,?)",
                (model_name, json.dumps(body.tags), json.dumps(body.custom_tags), now()),
            )
    return {"ok": True, "model_name": model_name, "tags": body.tags, "custom_tags": body.custom_tags}


@router.post("/{model_name}/auto")
def auto_tag(model_name: str):
    """自动打标并保存"""
    tags = _auto_tags(model_name)
    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM model_tags WHERE model_name=?", (model_name,)).fetchone()
        if existing:
            conn.execute("UPDATE model_tags SET tags=?, updated_at=? WHERE model_name=?", (json.dumps(tags), now(), model_name))
        else:
            conn.execute(
                "INSERT INTO model_tags (model_name, tags, custom_tags, updated_at) VALUES (?,?,?,?)",
                (model_name, json.dumps(tags), "[]", now()),
            )
    return {"ok": True, "model_name": model_name, "tags": tags}
