"""空闲自动卸载管理 - 后台任务：定期检查已加载模型，无调用超过阈值自动卸载

- 阈值：services.idle_unload_min（分钟），0 = 一直保持不卸载
- 最后调用时间：services.last_used_at（v1_proxy / chat 代理时更新）
- 检查周期：每 30 秒一次
"""
import logging
import time
from pathlib import Path

from app.config import settings
from app.database import get_conn, now

logger = logging.getLogger("idle-unload")

CHECK_INTERVAL = 30  # 秒


def touch_model_usage(model_name: str):
    """记录模型被调用（更新 last_used_at）"""
    if not model_name:
        return
    try:
        with get_conn() as conn:
            conn.execute(
                "UPDATE services SET last_used_at=? WHERE name=?",
                (int(time.time()), model_name),
            )
    except Exception:
        pass


def _check_once():
    """执行一次检查：找出超时的已加载模型并卸载"""
    from app import router_client

    unloaded = []
    try:
        # 已加载模型列表
        loaded_info = router_client.get_loaded_models_sync()
        items = loaded_info
        if isinstance(loaded_info, dict):
            items = loaded_info.get("data", [])
        loaded_ids = set()
        if isinstance(items, list):
            for m in items:
                st = m.get("status") if isinstance(m.get("status"), dict) else {}
                if st.get("value") == "loaded":
                    loaded_ids.add(m.get("id", ""))
        if not loaded_ids:
            return unloaded

        # 查所有服务的空闲阈值和最后调用时间
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT name, model_path, idle_unload_min, last_used_at FROM services"
            ).fetchall()
            del_names = {r["name"] for r in conn.execute("SELECT name FROM deleted_models").fetchall()}

        t_now = int(time.time())
        for r in rows:
            d = dict(r)
            name = d["name"]
            if name in del_names:
                continue
            if name not in loaded_ids:
                continue
            idle_min = d.get("idle_unload_min") or 0
            if idle_min <= 0:
                continue  # 一直保持

            last_used = d.get("last_used_at") or 0
            idle_seconds = t_now - last_used
            if idle_seconds >= idle_min * 60:
                # 匹配 router ID 并卸载
                try:
                    from app.routers.services import _match_router_id
                    router_id = _match_router_id(d.get("model_path", "")) or name
                    router_client.unload_model_sync(router_id)
                    with get_conn() as conn:
                        conn.execute(
                            "UPDATE services SET status='unloaded', updated_at=? WHERE name=?",
                            (now(), name),
                        )
                    unloaded.append({"model": name, "idle_minutes": idle_min})
                    logger.info("空闲超时自动卸载模型: %s（空闲 %ds > %dmin）", name, idle_seconds, idle_min)
                except Exception as e:
                    logger.warning("自动卸载 %s 失败: %s", name, e)
    except Exception as e:
        logger.error("idle check 异常: %s", e)
    return unloaded


def start_idle_unload_loop():
    """启动后台空闲卸载检查循环（守护线程）"""
    import threading

    def _loop():
        while True:
            try:
                _check_once()
            except Exception:
                pass
            time.sleep(CHECK_INTERVAL)

    t = threading.Thread(target=_loop, name="idle-unload", daemon=True)
    t.start()
    logger.info("空闲自动卸载检查已启动（周期 %ds）", CHECK_INTERVAL)
    return t
