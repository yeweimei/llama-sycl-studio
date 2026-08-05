"""自愈监控 - 后台任务：检测实例异常并自动重启（M3）

- 检测目标：services 表中 status='loaded' 的服务，其实例进程死亡或
  /health 连续失败（degraded）时自动拉起
- 防抖：连续 N 次重启仍失败则标记 error 并暂停（避免疯狂重启循环）
- 与 idle_unload 的关系：idle 负责"该停的停"，自愈负责"该活的活"；
  空闲超时卸载会把 status 置为 'unloaded'，自愈不干预 unloaded 的服务
- 检查周期：每 20 秒一次
"""
import logging
import time

from app.database import get_conn, now

logger = logging.getLogger("self-heal")

CHECK_INTERVAL = 20  # 秒
MAX_CONSECUTIVE_FAILURES = 3  # 连续重启失败次数上限
DEAD_SECONDS = 5  # 进程死亡多久后开始自愈（避免正常停止的瞬间误判）
HEALTH_FAIL_THRESHOLD = 2  # degraded 状态连续观察到 N 次才自愈（单次抖动不触发）
# 加载/预热保护窗口（秒）：实例启动后此窗口内不因 degraded 触发自愈。
# 实测：容器重建后首次推理预热需 ~90-100s（flash-attn 内核首次执行/图编译），
# 加载本身冷启动也需数十秒；窗口过短会把正常加载中的实例误杀重启（重启风暴放大器）。
# 窗口内只观察，窗口外才判定真故障。
STARTUP_GRACE_SECONDS = 240

# 内存态: {sid: {"consecutive_fails": int, "last_seen_degraded": int, "last_heal_at": int, "_degraded_count": int}}
_state: dict[int, dict] = {}

# 退避策略：连续失败后拉长重启间隔（秒），避免 GPU 脏状态下的重启风暴
_BACKOFF_STEPS = [0, 20, 60, 120]  # 第 1/2/3+ 次失败的等待间隔


def _should_service_be_loaded(sid: int, name: str) -> bool:
    """该服务是否应该处于加载状态（status='loaded' 且未被删除）"""
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT status FROM services WHERE id=?", (sid,)
            ).fetchone()
            if not row:
                return False
            del_name = conn.execute(
                "SELECT name FROM deleted_models WHERE name=?", (name,)
            ).fetchone()
            if del_name:
                return False
            return row["status"] == "loaded"
    except Exception:
        return False


def _heal_once():
    """执行一次自愈检查"""
    from app import instance_mgr

    healed = []
    try:
        # 所有已注册服务（含未加载的）
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id, name, model_path FROM services"
            ).fetchall()

        t_now = int(time.time())
        for r in rows:
            d = dict(r)
            sid, name, model_path = d["id"], d["name"], d["model_path"]
            if not _should_service_be_loaded(sid, name):
                # 服务不应加载（unloaded/error），重置状态
                _state.pop(sid, None)
                continue

            st = instance_mgr.instance_status(sid)
            if st.get("state") == "running":
                _state.pop(sid, None)
                continue

            s = _state.setdefault(sid, {"consecutive_fails": 0, "last_seen_bad": 0, "last_heal_at": 0})
            if st.get("state") == "stopped":
                # 进程死了
                if t_now - s["last_seen_bad"] > DEAD_SECONDS:
                    s["consecutive_fails"] += 1
                s["last_seen_bad"] = t_now
            elif st.get("state") == "degraded":
                # 加载/预热保护：启动后窗口内 degraded 属正常（加载中/首推预热），不触发自愈
                started_at = st.get("started_at") or 0
                if started_at and (t_now - started_at) < STARTUP_GRACE_SECONDS:
                    s["_degraded_count"] = 0  # 窗口内计数清零，窗口外重新计
                    continue
                # 窗口外进程活但 /health 不通：连续观察 HEALTH_FAIL_THRESHOLD 次才自愈
                s["last_seen_bad"] = t_now
                if s.get("_degraded_count", 0) < HEALTH_FAIL_THRESHOLD:
                    s["_degraded_count"] = s.get("_degraded_count", 0) + 1
                    continue
                s["consecutive_fails"] += 1
            else:
                continue

            # 连续失败超限 → 标记 error，暂停自愈
            if s["consecutive_fails"] > MAX_CONSECUTIVE_FAILURES:
                logger.error("自愈放弃 %s：连续 %d 次重启失败，标记 error", name, s["consecutive_fails"])
                try:
                    with get_conn() as conn:
                        conn.execute(
                            "UPDATE services SET status='error', updated_at=? WHERE id=?",
                            (now(), sid),
                        )
                except Exception:
                    pass
                _state.pop(sid, None)
                continue

            # 退避：连续失败后拉长间隔，避免重启风暴
            backoff = _BACKOFF_STEPS[min(s["consecutive_fails"], len(_BACKOFF_STEPS) - 1)]
            if backoff and t_now - s.get("last_heal_at", 0) < backoff:
                continue

            # 执行自愈重启
            try:
                result = instance_mgr.start_instance(sid, name, model_path)
                s["last_heal_at"] = t_now
                healed.append({"model": name, "state": st.get("state"), "result": result.get("status")})
                logger.warning("自愈重启 %s（状态 %s，第 %d 次）→ %s", name, st.get("state"), s["consecutive_fails"], result.get("status"))
            except Exception as e:
                logger.warning("自愈重启 %s 失败: %s", name, e)
    except Exception as e:
        logger.error("self-heal check 异常: %s", e)
    return healed


def start_self_heal_loop():
    """启动后台自愈检查循环（守护线程）"""
    import threading

    def _loop():
        while True:
            try:
                _heal_once()
            except Exception:
                pass
            time.sleep(CHECK_INTERVAL)

    t = threading.Thread(target=_loop, name="self-heal", daemon=True)
    t.start()
    logger.info("自愈监控已启动（周期 %ds，最多连续重启 %d 次）", CHECK_INTERVAL, MAX_CONSECUTIVE_FAILURES)
    return t
