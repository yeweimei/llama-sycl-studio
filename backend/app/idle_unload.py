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
    """执行一次检查：找出超时的已运行实例并停止"""
    from app import instance_mgr

    unloaded = []
    try:
        # 运行中的实例
        inst_map = instance_mgr.all_instances()
        if not inst_map:
            return unloaded

        # 查所有服务的空闲阈值和最后调用时间
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id, name, model_path, idle_unload_min, last_used_at FROM services"
            ).fetchall()
            del_names = {r["name"] for r in conn.execute("SELECT name FROM deleted_models").fetchall()}

        t_now = int(time.time())
        for r in rows:
            d = dict(r)
            name = d["name"]
            if name in del_names:
                continue
            sid = d["id"]
            if sid not in inst_map:
                continue
            idle_min = d.get("idle_unload_min") or 0
            if idle_min <= 0:
                continue  # 一直保持

            last_used = d.get("last_used_at") or 0
            idle_seconds = t_now - last_used
            if idle_seconds >= idle_min * 60:
                try:
                    instance_mgr.stop_instance(sid)
                    with get_conn() as conn:
                        conn.execute(
                            "UPDATE services SET status='unloaded', updated_at=? WHERE id=?",
                            (now(), sid),
                        )
                    unloaded.append({"model": name, "idle_minutes": idle_min})
                    logger.info("空闲超时自动卸载模型: %s（空闲 %ds > %dmin）", name, idle_seconds, idle_min)
                except Exception as e:
                    logger.warning("自动卸载 %s 失败: %s", name, e)
    except Exception as e:
        logger.error("idle check 异常: %s", e)
    return unloaded
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
