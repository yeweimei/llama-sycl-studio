"""API 调用统计 API"""
import json
from fastapi import APIRouter
from app.database import get_conn, now

router = APIRouter()


def _record_stats(model_name: str, prompt_tokens: int = 0, completion_tokens: int = 0,
                  prefill_ms: int = 0, decode_ms: int = 0):
    """记录一次 API 调用统计"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM api_stats WHERE model_name=?", (model_name,)).fetchone()
        if row:
            d = dict(row)
            conn.execute(
                "UPDATE api_stats SET request_count=?, prompt_tokens=?, completion_tokens=?, "
                "total_prefill_ms=?, total_decode_ms=?, updated_at=? WHERE model_name=?",
                (d["request_count"] + 1, d["prompt_tokens"] + prompt_tokens,
                 d["completion_tokens"] + completion_tokens,
                 d["total_prefill_ms"] + prefill_ms, d["total_decode_ms"] + decode_ms,
                 now(), model_name),
            )
        else:
            conn.execute(
                "INSERT INTO api_stats (model_name, request_count, prompt_tokens, completion_tokens, "
                "total_prefill_ms, total_decode_ms, updated_at) VALUES (?,?,?,?,?,?,?)",
                (model_name, 1, prompt_tokens, completion_tokens, prefill_ms, decode_ms, now()),
            )


@router.get("")
def list_stats():
    """返回 API 调用统计聚合列表"""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM api_stats ORDER BY request_count DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        rc = d["request_count"] or 1
        d["avg_prefill_ms"] = round(d["total_prefill_ms"] / rc, 1) if d["total_prefill_ms"] else 0
        d["avg_decode_ms"] = round(d["total_decode_ms"] / rc, 1) if d["total_decode_ms"] else 0
        out.append(d)
    return out
