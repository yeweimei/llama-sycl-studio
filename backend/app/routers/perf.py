"""推理性能聚合 API - 遍历 loaded 实例拉取 llama-server /metrics（需 --metrics 启动参数）

返回每模型：实时吞吐（decode/prefill t/s）、MTP 投机接受率、请求队列、累计 tokens。
"""
import logging
import time

import httpx
from fastapi import APIRouter

from app.database import get_conn

logger = logging.getLogger("perf")

router = APIRouter()

# /metrics 是 Prometheus 文本格式，这里只解析需要的 llamacpp: 前缀指标
_METRIC_KEYS = {
    "prompt_tokens_total": "prompt_tokens_total",
    "prompt_seconds_total": "prompt_seconds_total",
    "tokens_predicted_total": "tokens_predicted_total",
    "tokens_predicted_seconds_total": "tokens_predicted_seconds_total",
    "spec_decode_num_draft_tokens_total": "draft_tokens_total",
    "spec_decode_num_accepted_tokens_total": "accepted_tokens_total",
    "spec_decode_num_drafts_total": "drafts_total",
    "prompt_tokens_seconds": "prefill_tps",
    "predicted_tokens_seconds": "decode_tps",
    "requests_processing": "requests_processing",
    "requests_deferred": "requests_deferred",
    "n_busy_slots_per_decode": "busy_slots",
}


def _parse_metrics(text: str) -> dict:
    """解析 /metrics 文本，提取 llamacpp: 指标（gauge 取当前值，counter 取累计值）"""
    out: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("llamacpp:"):
            continue
        # 形如: llamacpp:predicted_tokens_seconds 12.34
        try:
            name, val = line.split()
            short = name.split(":", 1)[1]
            if short in _METRIC_KEYS:
                out[_METRIC_KEYS[short]] = float(val)
        except (ValueError, IndexError):
            continue
    return out


@router.get("/instances")
def perf_instances():
    """聚合所有 loaded 实例的实时性能指标"""
    from app.instance_mgr import BASE_PORT

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, status FROM services WHERE status='loaded'"
        ).fetchall()

    result = []
    now = time.time()
    with httpx.Client(timeout=4.0) as c:
        for r in rows:
            port = BASE_PORT + int(r["id"]) - 1
            item = {
                "model": r["name"],
                "port": port,
                "online": False,
                "decode_tps": None,
                "prefill_tps": None,
                "mtp_accept": None,      # 0~1
                "draft_tokens_total": 0,
                "accepted_tokens_total": 0,
                "requests_processing": 0,
                "requests_deferred": 0,
                "busy_slots": 0,
                "prompt_tokens_total": 0,
                "predicted_tokens_total": 0,
                "fetched_at": now,
            }
            try:
                resp = c.get(f"http://127.0.0.1:{port}/metrics")
                if resp.status_code != 200:
                    result.append(item)
                    continue
                m = _parse_metrics(resp.text)
                item["online"] = True
                item["decode_tps"] = round(m.get("decode_tps", 0), 2)
                item["prefill_tps"] = round(m.get("prefill_tps", 0), 2)
                item["requests_processing"] = int(m.get("requests_processing", 0))
                item["requests_deferred"] = int(m.get("requests_deferred", 0))
                item["busy_slots"] = round(m.get("busy_slots", 0), 2)
                item["prompt_tokens_total"] = int(m.get("prompt_tokens_total", 0))
                item["predicted_tokens_total"] = int(m.get("tokens_predicted_total", 0))
                item["draft_tokens_total"] = int(m.get("draft_tokens_total", 0))
                item["accepted_tokens_total"] = int(m.get("accepted_tokens_total", 0))
                dt = m.get("draft_tokens_total", 0)
                item["mtp_accept"] = round(m.get("accepted_tokens_total", 0) / dt, 4) if dt > 0 else None
                # 平均吞吐（累计口径，比瞬时 gauge 更稳）
                pt = m.get("prompt_seconds_total", 0)
                pd = m.get("tokens_predicted_seconds_total", 0)
                if pt > 0:
                    item["prefill_tps"] = round(m.get("prompt_tokens_total", 0) / pt, 2)
                if pd > 0:
                    item["decode_tps"] = round(m.get("tokens_predicted_total", 0) / pd, 2)
            except Exception as e:
                logger.warning("perf fetch %s:%s failed: %s", r["name"], port, e)
            result.append(item)
    return {"instances": result, "generated_at": now}


@router.get("/history")
def perf_history(minutes: int = 10, points: int = 60):
    """最近性能采样历史（前端趋势图；当前返回空骨架，采样由前端轮询累积）"""
    return {"minutes": minutes, "points": points, "samples": []}
