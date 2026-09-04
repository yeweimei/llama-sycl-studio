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
from pathlib import Path

from app.config import settings
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
# error 状态冷却期（秒）：自愈放弃并标记 error 后，冷却期满自动恢复
# loaded 重试一次（避免永久 error 需手动干预，又不至于疯狂重启）
ERROR_RETRY_SECONDS = 600

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


def _should_retry_error_by_db(sid: int, t_now: int) -> bool:
    """DB 冷却重试判断：error 状态且 updated_at 超过 ERROR_RETRY_SECONDS"""
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT status, updated_at FROM services WHERE id=?", (sid,)
            ).fetchone()
            if not row or row["status"] != "error":
                return False
            return (t_now - int(row["updated_at"])) >= ERROR_RETRY_SECONDS
    except Exception:
        return False


def _retry_error_service(sid: int, name: str):
    """error 冷却期满：恢复 loaded 重新尝试"""
    logger.warning("自愈重试 %s：error 冷却期满，恢复 loaded 重新尝试", name)
    try:
        with get_conn() as conn:
            conn.execute(
                "UPDATE services SET status='loaded', updated_at=? WHERE id=?",
                (now(), sid),
            )
    except Exception:
        pass


# 告警节流：{key: last_ts}，同 key 5 分钟内不重复发送
_alert_throttle: dict[str, float] = {}
_ALERT_THROTTLE_SECONDS = 300


def _send_alert(title: str, message: str):
    """发送告警（带节流：同标题 5 分钟最多 1 条）"""
    import time as _t
    key = title
    now_ts = _t.time()
    if now_ts - _alert_throttle.get(key, 0) < _ALERT_THROTTLE_SECONDS:
        return
    _alert_throttle[key] = now_ts
    try:
        from app import alert
        alert.send_alert(title, message)
    except Exception:
        pass


# 崩溃签名关键词（从实例日志识别 OOM / IGC 编译崩溃 / device-lost 等，便于复盘根因）
_CRASH_SIGS = [
    "IGC:", "Internal Compiler", "FLASH_ATTN_EXT", "DEVICE_LOST",
    "UR_RESULT_ERROR", "OUT_OF_MEMORY", "alloc failed", "failed to allocate",
    "SIGSEGV", "segfault", "abort", "Assertion", "CUDA error", "CL_INVALID",
]


def _capture_crash(sid: int, name: str, state: str) -> str:
    """实例异常重启前：抓日志尾部崩溃签名 → 落库 instance_crashes → 返回摘要。
    便于复盘根因（OOM vs IGC 编译崩溃 vs device-lost），并附带进自愈告警。"""
    sig_hits, tail = [], ""
    try:
        log_path = Path(settings.data_dir) / "instances" / f"{name}.log"
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            tail = "\n".join(lines[-80:])[-4000:]
            for ln in reversed(lines[-250:]):
                for s in _CRASH_SIGS:
                    if s in ln and s not in sig_hits:
                        sig_hits.append(s)
    except Exception:
        pass
    signature = ", ".join(sig_hits)
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO instance_crashes (service_id, model_name, crash_signature, log_tail, state, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (sid, name, signature, tail, state, int(time.time())),
            )
    except Exception:
        pass
    return signature


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
                # 服务不应加载（unloaded/error）
                s = _state.get(sid)
                if s and s.get("marked_error"):
                    # 内存态冷却重试（同进程内有效）
                    if t_now - s.get("error_at", 0) >= ERROR_RETRY_SECONDS:
                        _retry_error_service(sid, name)
                        _state.pop(sid, None)
                elif _should_retry_error_by_db(sid, t_now):
                    # DB 冷却重试（跨重启/内存态丢失场景）：error 状态且
                    # updated_at 超过冷却期 → 自动恢复 loaded
                    _retry_error_service(sid, name)
                else:
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

            # 连续失败超限 → 标记 error，暂停自愈（冷却期后自动重试一次）
            if s["consecutive_fails"] > MAX_CONSECUTIVE_FAILURES:
                if not s.get("marked_error"):
                    logger.error("自愈放弃 %s：连续 %d 次重启失败，标记 error（%ds 后自动重试）", name, s["consecutive_fails"], ERROR_RETRY_SECONDS)
                    _send_alert(f"模型 {name} 自愈失败", f"连续 {s['consecutive_fails']} 次重启失败，已标记 error，{ERROR_RETRY_SECONDS}s 后自动重试")
                    try:
                        with get_conn() as conn:
                            conn.execute(
                                "UPDATE services SET status='error', updated_at=? WHERE id=?",
                                (now(), sid),
                            )
                    except Exception:
                        pass
                    s["marked_error"] = True
                    s["error_at"] = t_now
                # 冷却期后自动恢复 loaded 重试（避免永久 error 需手动干预）
                if t_now - s.get("error_at", 0) >= ERROR_RETRY_SECONDS:
                    logger.warning("自愈重试 %s：error 冷却期满，恢复 loaded 重新尝试", name)
                    try:
                        with get_conn() as conn:
                            conn.execute(
                                "UPDATE services SET status='loaded', updated_at=? WHERE id=?",
                                (now(), sid),
                            )
                    except Exception:
                        pass
                    _state.pop(sid, None)  # 重置计数，重新观察
                continue

            # 退避：连续失败后拉长间隔，避免重启风暴
            backoff = _BACKOFF_STEPS[min(s["consecutive_fails"], len(_BACKOFF_STEPS) - 1)]
            if backoff and t_now - s.get("last_heal_at", 0) < backoff:
                continue

            # 执行自愈重启
            try:
                _sig = _capture_crash(sid, name, st.get("state"))
                result = instance_mgr.start_instance(sid, name, model_path)
                s["last_heal_at"] = t_now
                healed.append({"model": name, "state": st.get("state"), "result": result.get("status")})
                logger.warning("自愈重启 %s（状态 %s，第 %d 次）→ %s%s", name, st.get("state"), s["consecutive_fails"], result.get("status"), (f"（{_sig}）" if _sig else ""))
                _send_alert(f"模型 {name} 已自愈重启", f"状态 {st.get('state')} → {result.get('status')}（第 {s['consecutive_fails']} 次）{('可能原因：'+_sig) if _sig else ''}")
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
