"""API 调用统计 API"""
import json
import time as _t
from typing import Optional

from fastapi import APIRouter, Query
from app.database import get_conn, now

router = APIRouter()


def _record_stats(model_name: str, prompt_tokens: int = 0, completion_tokens: int = 0,
                  prefill_ms: int = 0, decode_ms: int = 0,
                  stream: bool = False, ok: bool = True, status_code: int = 200,
                  total_ms: int = 0, error: str = "",
                  endpoint: str = "", method: str = "POST"):
    """记录一次 API 调用统计（聚合表 + 请求明细表）

    endpoint: 对外 API 路径（如 /v1/chat/completions），用于端点维度统计；
    method: HTTP 方法（GET/POST/...）。"""
    t_now = int(_t.time())
    total_ms = total_ms or (prefill_ms + decode_ms)
    try:
        with get_conn() as conn:
            # 聚合表
            row = conn.execute("SELECT * FROM api_stats WHERE model_name=?", (model_name,)).fetchone()
            if row:
                d = dict(row)
                conn.execute(
                    "UPDATE api_stats SET request_count=?, ok_count=?, fail_count=?, prompt_tokens=?, "
                    "completion_tokens=?, total_prefill_ms=?, total_decode_ms=?, updated_at=? "
                    "WHERE model_name=?",
                    (d["request_count"] + 1, (d.get("ok_count") or 0) + (1 if ok else 0),
                     (d.get("fail_count") or 0) + (0 if ok else 1),
                     d["prompt_tokens"] + prompt_tokens,
                     d["completion_tokens"] + completion_tokens,
                     d["total_prefill_ms"] + prefill_ms, d["total_decode_ms"] + decode_ms,
                     now(), model_name),
                )
            else:
                conn.execute(
                    "INSERT INTO api_stats (model_name, request_count, ok_count, fail_count, "
                    "prompt_tokens, completion_tokens, total_prefill_ms, total_decode_ms, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (model_name, 1, 1 if ok else 0, 0 if ok else 1, prompt_tokens, completion_tokens,
                     prefill_ms, decode_ms, now()),
                )
            # 请求明细表（含端点/方法维度）
            conn.execute(
                "INSERT INTO api_request_logs (model_name, stream, ok, status_code, prompt_tokens, "
                "completion_tokens, total_ms, prefill_ms, decode_ms, error, endpoint, method, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (model_name, 1 if stream else 0, 1 if ok else 0, status_code,
                 prompt_tokens, completion_tokens, total_ms, prefill_ms, decode_ms,
                 (error or "")[:500], (endpoint or "")[:200], (method or "POST")[:20], t_now),
            )
    except Exception:
        pass


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


@router.get("/trends")
def stats_trends(hours: int = Query(24, ge=1, le=168), bucket_minutes: int = Query(60, ge=5, le=1440)):
    """按时间桶聚合请求趋势（QPS/延迟/token）"""
    bucket_s = bucket_minutes * 60
    t_now = int(_t.time())
    since = t_now - hours * 3600
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT created_at, ok, prompt_tokens, completion_tokens, total_ms, prefill_ms, decode_ms "
            "FROM api_request_logs WHERE created_at>=? ORDER BY created_at",
            (since,),
        ).fetchall()
    # 按桶聚合
    buckets: dict[int, dict] = {}
    for r in rows:
        b = (r["created_at"] // bucket_s) * bucket_s
        d = buckets.setdefault(b, {"requests": 0, "ok": 0, "fail": 0, "prompt": 0, "completion": 0,
                                   "total_ms": 0, "prefill_ms": 0, "decode_ms": 0})
        d["requests"] += 1
        d["ok"] += 1 if r["ok"] else 0
        d["fail"] += 0 if r["ok"] else 1
        d["prompt"] += r["prompt_tokens"] or 0
        d["completion"] += r["completion_tokens"] or 0
        d["total_ms"] += r["total_ms"] or 0
        d["prefill_ms"] += r["prefill_ms"] or 0
        d["decode_ms"] += r["decode_ms"] or 0
    out = []
    for b in sorted(buckets):
        d = buckets[b]
        n = d["requests"] or 1
        out.append({
            "ts": b,
            "requests": d["requests"],
            "ok": d["ok"],
            "fail": d["fail"],
            "qps": round(d["requests"] / bucket_s, 3),
            "prompt_tokens": d["prompt"],
            "completion_tokens": d["completion"],
            "avg_total_ms": round(d["total_ms"] / n, 1),
            "avg_prefill_ms": round(d["prefill_ms"] / n, 1),
            "avg_decode_ms": round(d["decode_ms"] / n, 1),
        })
    return {"buckets": out, "bucket_seconds": bucket_s, "since": since}


@router.get("/endpoints")
def endpoint_stats(hours: int = Query(0, ge=0, le=720)):
    """按 API 端点聚合统计：次数/成功率/状态码分布/平均延迟/token。
    hours=0 表示全部时间。"""
    where = ""
    params: list = []
    if hours > 0:
        since = int(_t.time()) - hours * 3600
        where = "WHERE created_at>=?"
        params.append(since)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT endpoint, method, status_code, ok, prompt_tokens, completion_tokens, total_ms "
            f"FROM api_request_logs {where}",
            tuple(params),
        ).fetchall()
    agg: dict[str, dict] = {}
    for r in rows:
        ep = r["endpoint"] or "(unknown)"
        d = agg.setdefault(ep, {"endpoint": ep, "method": (r["method"] or "POST").upper(),
                                "requests": 0, "ok": 0, "fail": 0,
                                "prompt_tokens": 0, "completion_tokens": 0,
                                "total_ms": 0, "status_codes": {}})
        d["requests"] += 1
        d["ok"] += 1 if r["ok"] else 0
        d["fail"] += 0 if r["ok"] else 1
        d["prompt_tokens"] += r["prompt_tokens"] or 0
        d["completion_tokens"] += r["completion_tokens"] or 0
        d["total_ms"] += r["total_ms"] or 0
        sc = str(r["status_code"] or 0)
        d["status_codes"][sc] = d["status_codes"].get(sc, 0) + 1
    out = []
    for ep, d in agg.items():
        n = d["requests"] or 1
        d["success_rate"] = round(d["ok"] / n * 100, 1)
        d["avg_total_ms"] = round(d["total_ms"] / n, 1)
        # 状态码分布转列表（降序）
        d["status_code_list"] = [{"code": int(k), "count": v} for k, v in
                                  sorted(d["status_codes"].items(), key=lambda x: -x[1])]
        d.pop("status_codes", None)
        d.pop("total_ms", None)
        out.append(d)
    out.sort(key=lambda x: -x["requests"])
    return {"endpoints": out, "total": sum(x["requests"] for x in out)}


@router.get("/requests")
def recent_requests(limit: int = Query(50, ge=1, le=500)):
    """最近请求明细（含失败记录）"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM api_request_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        out.append(d)
    return out


@router.get("/chat-logs")
def chat_logs(limit: int = Query(100, ge=1, le=500), model: Optional[str] = None):
    """对话内容日志（chat_api_logs），最新在前；running 状态优先包含

    model: 可选，按模型名精确过滤（服务详情页用）
    """
    where = ""
    params: list = []
    if model:
        where = "AND model_name=?"
        params.append(model)
    with get_conn() as conn:
        # 先取 running（进行中），再补最近 done/error
        running = conn.execute(
            f"SELECT * FROM chat_api_logs WHERE status='running' {where} ORDER BY id DESC",
            tuple(params),
        ).fetchall()
        done = conn.execute(
            f"SELECT * FROM chat_api_logs WHERE 1=1 {where} ORDER BY id DESC LIMIT ?",
            tuple(params) + (limit,),
        ).fetchall()
    out = []
    seen = set()
    # running 优先
    for r in running:
        d = dict(r)
        d["id"] = int(d["id"])
        seen.add(d["id"])
        out.append(d)
    # 再补最近记录（去重）
    for r in done:
        d = dict(r)
        d["id"] = int(d["id"])
        if d["id"] not in seen:
            seen.add(d["id"])
            out.append(d)
    return {"items": out[:limit]}


@router.get("/chat-log-models")
def chat_log_models():
    """返回有对话日志记录的模型列表（去重）"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT model_name FROM chat_api_logs ORDER BY model_name"
        ).fetchall()
    return {"models": [r[0] for r in rows if r[0]]}


@router.delete("/chat-logs")
def clear_chat_logs(model: Optional[str] = None):
    """清空对话日志（可选按模型）"""
    with get_conn() as conn:
        if model:
            conn.execute("DELETE FROM chat_api_logs WHERE model_name=?", (model,))
        else:
            conn.execute("DELETE FROM chat_api_logs")
    return {"ok": True}
