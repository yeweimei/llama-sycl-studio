"""自愈状态机（backend/app/self_heal.py）单元测试。

批次一新增：stalled 自愈路径（推理活性探活 → running 且 stalled_sids 含它 → 走自愈分支）。

避坑约定（参考 test_instance_slots.py 风格）：
- `_heal_once()` 内部 `from app import instance_mgr`，拿的是模块对象，
  所以必须 monkeypatch `instance_mgr.stalled_sids / instance_status / start_instance`
  这三个**函数**，不能 patch `sh.instance_mgr`（那样不会生效）。
- 状态在 `sh._state`（dict），conftest 的 autouse reset_internal_state 已每用例清空。
- DB 用 conftest 的 `init_db` fixture；本文件再加 autouse `clean_db` 清 services 等表，
  保证每用例只剩自己插入的那条服务（避免跨用例残留行干扰全表扫描）。
- 用可控制时钟 `fake_clock` patch `sh.time.time`（即 stdlib time.time，`_heal_once`
  的 t_now 与 database.now/updated_at 都受它控制），不真睡长。
- 可 monkeypatch 模块常量（HEALTH_FAIL_THRESHOLD / STARTUP_GRACE_SECONDS /
  MAX_CONSECUTIVE_FAILURES / ERROR_RETRY_SECONDS / DEAD_SECONDS）加速并隔离。
"""
import pytest

from app import instance_mgr
from app import self_heal as sh
from app.database import get_conn


# ---------------------------------------------------------------- 工具 ----
class FakeClock:
    """可控时钟：驱动 t_now / updated_at / 退避 / 冷却 / 启动窗口判定。

    用大起点（1e7）让 `t_now - 0 > DEAD_SECONDS` 等首笔判定天然成立。
    """

    def __init__(self, start: int = 10_000_000):
        self.t = start

    def time(self) -> float:
        return self.t

    def advance(self, s: int) -> None:
        self.t += s


@pytest.fixture
def fake_clock(monkeypatch):
    clk = FakeClock()
    # `_heal_once` 用的 `import time` 与 database.now 共享 stdlib time 模块对象，
    # 所以 patch `sh.time.time` 一个点即可同时驱动 t_now 与 DB updated_at 语义。
    monkeypatch.setattr(sh.time, "time", clk.time)
    return clk


@pytest.fixture(autouse=True)
def clean_db(init_db):
    """每用例清空自愈会扫的表，只保留本用例插入的服务。"""
    with get_conn() as conn:
        conn.execute("DELETE FROM services")
        conn.execute("DELETE FROM deleted_models")
        conn.execute("DELETE FROM instance_crashes")
    yield


def _add_service(fp: FakeClock, sid: int, name: str = "m1",
                 status: str = "loaded", updated_at: int | None = None) -> None:
    """插入一条服务记录；updated_at 默认取当前假时钟时间。"""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO services (id, name, model_path, status, updated_at) "
            "VALUES (?,?,?,?,?)",
            (sid, name, f"/models/{name}", status,
             int(fp.t) if updated_at is None else updated_at),
        )


def _status(name: str) -> str:
    """查一条服务的当前状态。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM services WHERE name=?", (name,)
        ).fetchone()
    return row["status"] if row else None


class FakeIM:
    """instance_mgr 三接口的替身：record 每次 start_instance 调用。"""

    def __init__(self):
        self.starts = []  # [(sid, name, model_path)]
        self._status = {"state": "unloaded"}
        self._stalled = []

    def stalled_sids(self):
        return list(self._stalled)

    def instance_status(self, sid):
        return dict(self._status)

    def start_instance(self, sid, name, model_path):
        self.starts.append((sid, name, model_path))
        return {"status": "ok"}


@pytest.fixture
def fake_im(monkeypatch):
    im = FakeIM()
    monkeypatch.setattr(instance_mgr, "stalled_sids", im.stalled_sids)
    monkeypatch.setattr(instance_mgr, "instance_status", im.instance_status)
    monkeypatch.setattr(instance_mgr, "start_instance", im.start_instance)
    return im


# ================================================= 1. 防抖（degraded） =====
def test_degraded_debounce_heals_after_threshold(fake_clock, fake_im, clean_db, monkeypatch):
    """防抖：degraded 单次(<阈值)抖动不自愈，需连续观察满 HEALTH_FAIL_THRESHOLD 次才自愈。

    源语义：计数器在 _degraded_count < 阈值期间逐轮自增并 continue；当计数器已满
    （第 1 轮→1、第 2 轮→2）后，下一次 poll 才回落执行自愈。故 THRESHOLD=2 时第 1、2 轮
    都只观察，第 3 轮触发重启——即“必须连续观察到 ≥2 次才自愈，单次抖动绝不触发”。
    """
    monkeypatch.setattr(sh, "HEALTH_FAIL_THRESHOLD", 2)
    monkeypatch.setattr(sh, "STARTUP_GRACE_SECONDS", 0)
    _add_service(fake_clock, sid=1)
    fake_im._status = {"state": "degraded"}  # 进程活但 /health 不通

    # 第 1 次：计数 0→1(<2) → 只观察不自愈
    assert sh._heal_once() == []
    assert fake_im.starts == []
    assert sh._state[1]["_degraded_count"] == 1

    # 第 2 次：计数 1→2 仍未越界 → 依旧不自愈（单次/两次抖动都不该触发）
    assert sh._heal_once() == []
    assert fake_im.starts == []
    assert sh._state[1]["_degraded_count"] == 2

    # 第 3 次：计数器已满 → 自愈重启
    healed = sh._heal_once()
    assert len(healed) == 1
    assert healed[0]["model"] == "m1"
    assert healed[0]["state"] == "degraded"
    assert healed[0]["result"] == "ok"
    assert fake_im.starts == [(1, "m1", "/models/m1")]


# ============================================== 2. 启动保护窗口 ============
def test_startup_grace_skips_self_heal(fake_clock, fake_im, clean_db, monkeypatch):
    """启动 < STARTUP_GRACE_SECONDS 窗口内 degraded 属正常（预热编译），不触发自愈。

    即使重复观察多次，窗口内计数每轮清零，永不越界自愈。
    """
    monkeypatch.setattr(sh, "HEALTH_FAIL_THRESHOLD", 2)
    monkeypatch.setattr(sh, "STARTUP_GRACE_SECONDS", 240)
    _add_service(fake_clock, sid=1)
    fake_im._status = {"state": "degraded", "started_at": fake_clock.t - 100}  # 刚启动 100s(<240)

    for _ in range(5):
        assert sh._heal_once() == []
    assert fake_im.starts == []          # 从未自愈
    assert sh._state[1].get("_degraded_count", 0) == 0  # 窗口内计数保持清零
    assert sh._state[1]["consecutive_fails"] == 0


# ======================================== 3. stalled 自愈路径（批次一新增） ====
def test_stalled_running_heals_after_threshold(fake_clock, fake_im, clean_db, monkeypatch):
    """running 且 stalled_sids 含它 → 归因 stalled → 连续观察满阈值后 start_instance。"""
    monkeypatch.setattr(sh, "HEALTH_FAIL_THRESHOLD", 2)
    monkeypatch.setattr(sh, "STARTUP_GRACE_SECONDS", 0)
    _add_service(fake_clock, sid=1)
    fake_im._status = {"state": "running", "started_at": fake_clock.t - 1000}
    fake_im._stalled = [1]  # 有流量但长时间无成功推理 → 判 stalled

    # 第 1 次：计数 1 → 只观察
    assert sh._heal_once() == []
    assert fake_im.starts == []
    assert sh._state[1]["_stalled_count"] == 1

    # 第 2 次：计数 2 仍未越界 → 仍不自愈
    assert sh._heal_once() == []
    assert fake_im.starts == []

    # 第 3 次：计数器已满 → 走自愈分支重启
    healed = sh._heal_once()
    assert len(healed) == 1
    assert healed[0]["model"] == "m1"
    assert healed[0]["state"] == "stalled"
    assert fake_im.starts == [(1, "m1", "/models/m1")]


def test_running_healthy_not_stalled_does_nothing(fake_clock, fake_im, clean_db, monkeypatch):
    """running 且不在 stalled 集合 → 健康，不自愈、不累计。"""
    _add_service(fake_clock, sid=1)
    fake_im._status = {"state": "running", "started_at": fake_clock.t - 1000}
    fake_im._stalled = []

    assert sh._heal_once() == []
    assert fake_im.starts == []
    assert 1 not in sh._state  # 健康实例不留在内存态


# ================================================ 4. 退避策略 ==============
def test_backoff_steps_computation():
    """_BACKOFF_STEPS 按连续失败次数递增取间隔；越界封顶到最后一个值。"""
    steps = sh._BACKOFF_STEPS
    idx = lambda fails: steps[min(fails, len(steps) - 1)]
    assert [idx(i) for i in (0, 1, 2, 3, 4, 5)] == [0, 20, 60, 120, 120, 120]
    assert sh._BACKOFF_STEPS == [0, 20, 60, 120]  # 契约：顺序/取值不漂移


def test_backoff_skips_heal_until_interval_elapses(fake_clock, fake_im, clean_db, monkeypatch):
    """窗口内（距上次自愈 < 退避间隔）跳过重启；间隔后恢复；间隔随失败次数递增。"""
    monkeypatch.setattr(sh, "MAX_CONSECUTIVE_FAILURES", 100)  # 本测试只探退避，不触 error 分支
    monkeypatch.setattr(sh, "DEAD_SECONDS", 5)
    _add_service(fake_clock, sid=1)
    fake_im._status = {"state": "stopped"}  # 进程死了，每轮 consecutive_fails+1
    # 预置：已自愈过 1 次（last_heal_at=now），当前 1 次失败
    sh._state[1] = {"consecutive_fails": 1, "last_seen_bad": fake_clock.t - 100,
                    "last_heal_at": fake_clock.t}

    # 立刻再观察：consecutive_fails→2，backoff=60，距上次 0s<60 → 跳过
    assert sh._heal_once() == []
    assert fake_im.starts == []
    assert sh._state[1]["consecutive_fails"] == 2

    # 只过 60s：consecutive_fails→3，backoff=120，距上次 60s<120 → 仍跳过（间隔递增的证据）
    fake_clock.advance(60)
    assert sh._heal_once() == []
    assert fake_im.starts == []

    # 过满 120s：backoff=120 不拦截 → 真正重启
    fake_clock.advance(60)
    healed = sh._heal_once()
    assert len(healed) == 1
    assert fake_im.starts == [(1, "m1", "/models/m1")]


# ========================================== 5. error 冷却重试 =============
def test_error_db_cooldown_retry_restores_loaded(fake_clock, fake_im, clean_db, monkeypatch):
    """status=error 且 updated_at 超过 ERROR_RETRY_SECONDS → 自动恢复 loaded 重试。"""
    monkeypatch.setattr(sh, "ERROR_RETRY_SECONDS", 600)
    _add_service(fake_clock, sid=1, status="error", updated_at=fake_clock.t - 1000)

    healed = sh._heal_once()
    assert healed == []
    assert fake_im.starts == []                    # 冷却重试不是“启动”，不调 start_instance
    assert _status("m1") == "loaded"               # DB 端恢复 loaded

    # 恢复后再跑一遍：loaded 且 running 健康 → 什么都不做，且已从内存态清掉
    fake_im._status = {"state": "running"}
    assert sh._heal_once() == []
    assert 1 not in sh._state


def test_error_memory_cooldown_retry(fake_clock, fake_im, clean_db, monkeypatch):
    """内存态冷却重试：mark_error 后冷却期满自动恢复 loaded 并清状态。"""
    monkeypatch.setattr(sh, "ERROR_RETRY_SECONDS", 600)
    _add_service(fake_clock, sid=1, status="error", updated_at=fake_clock.t - 1000)
    sh._state[1] = {"marked_error": True, "error_at": fake_clock.t - 700}  # 700s>600 冷却满

    assert sh._heal_once() == []
    assert fake_im.starts == []
    assert _status("m1") == "loaded"   # 内存态冷却路径也恢复 loaded
    assert 1 not in sh._state          # retry 后重置计数/清状态


# ========================== 附加：连续失败超限 → 标记 error（防风暴） ======
def test_mark_error_after_max_consecutive_failures(fake_clock, fake_im, clean_db, monkeypatch):
    """连续重启失败超 MAX_CONSECUTIVE_FAILURES → 标记 DB error 并暂停自愈（防重启风暴）。"""
    monkeypatch.setattr(sh, "MAX_CONSECUTIVE_FAILURES", 3)
    monkeypatch.setattr(sh, "DEAD_SECONDS", 5)
    _add_service(fake_clock, sid=1)
    fake_im._status = {"state": "stopped"}

    # 每轮推进 130s（> DEAD_SECONDS 且 > 最大退避 120）：stopped 持续观测 →
    # consecutive_fails 逐轮 +1 且退避不拦截 → start 3 次后第 4 轮超限标 error。
    for _ in range(6):
        fake_clock.advance(130)
        sh._heal_once()
    assert _status("m1") == "error"          # DB 被标记 error
    assert fake_im.starts == [(1, "m1", "/models/m1")] * 3  # 只真重启了前面 3 次
    assert sh._state[1]["marked_error"] is True