"""并发闸（slot 许可）单元测试 — instance_mgr 的每模型并发控制。

覆盖：permit 守恒（历史 wait_for 竞态泄漏回归）、slot_limit、
release_slot 幂等/空闸静默、stalled 推理活性判定、stale slot 超时回收。

约定（避坑）：
- monkeypatch `im._preset_dict` 返回固定 parallel，不碰真实 DB。
- monkeypatch `im._ensure_reaper` 为 no-op，避免后台 asyncio 常驻任务
  跨用例绑在已关闭的 loop 上。
- conftest 的 autouse reset_internal_state 已每用例清空
  _instances/_slot_guard/_slot_held_since/_active_requests 等。
"""
import asyncio
import time

import pytest

from app import instance_mgr as im


# ---------------------------------------------------------------- 工具 ----
class FakeClock:
    """可控单调时钟，驱动 begin_request/stalled/stale 的时间判定。"""

    # 起始用非零值（如 1.0）：stall_info 的 `if req_t` 依赖 truthy 时刻，
    # 若从 0.0 开始，begin_request 写入 0.0 会被当作“无记录”。
    def __init__(self, start: float = 1.0):
        self.t = start

    def monotonic(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@pytest.fixture
def fake_clock(monkeypatch):
    clk = FakeClock()
    # 注意：模块内用的是 `time.monotonic()`，patch 共享 time 模块的属性
    monkeypatch.setattr(im.time, "monotonic", clk.monotonic)
    return clk


@pytest.fixture
def preset_parallel(monkeypatch):
    """把 _preset_dict 钉死为固定 parallel，隔离真实 DB。"""

    def _set(parallel):
        monkeypatch.setattr(im, "_preset_dict", lambda name: {"parallel": parallel})
        monkeypatch.setattr(im, "_ensure_reaper", lambda: None)  # 不启动后台 reaper

    return _set


# ===================================================== 1. permit 守恒 =====
async def test_timeout_does_not_leak_permit(preset_parallel):
    """超过 slot 上限 → 等待超时返回 False；随后 acquire 仍能拿到真 permit（不泄漏）。"""
    preset_parallel(1)
    sid, name = 7, "m1"

    assert await im.acquire_slot(sid, name, timeout=1) is True  # 独占唯一 slot

    # 第二路并发显著超出上限 → 快速超时，返回 False
    got = await im.acquire_slot(sid, name, timeout=0.05)
    assert got is False

    # 释放第一路持有的 permit；之前超时那路的回收任务应把其拿到/将拿到的 permit 归还
    im.release_slot(sid)
    await asyncio.sleep(0.05)  # 让 _reclaim_permit_after 的回收任务跑完

    g = im._slot_guard[sid]
    assert g._value == 1  # 计数守恒，回到满值
    # 新鲜 acquire 应当立刻成功（没有假性“并发已满”）
    assert await im.acquire_slot(sid, name, timeout=0.5) is True
    im.release_slot(sid)


async def test_reclaim_pending_acquire_does_not_lose_permit(preset_parallel):
    """竞态路径：acquire 仍在排队时时间到 → shield + 回收任务必须把 permit 归还。"""
    preset_parallel(1)
    sid = 8
    g = im._slot_guard_for(sid, "m")
    await g.acquire()  # 持有唯一 permit

    # 构造一个排队中的 acquire future（已被“超时”处理，交给回收兜底）
    fut = asyncio.ensure_future(g.acquire())
    im._reclaim_permit_after(fut, g)

    g.release()  # 释放持有的 permit → fut 获准
    await asyncio.sleep(0.05)  # 等回收任务 release

    # permit 未被吞掉：立即能再拿到
    ok = await asyncio.wait_for(g.acquire(), timeout=0.5)
    assert ok is True
    g.release()


async def test_many_concurrent_timeouts_conserve_permits(preset_parallel):
    """批量化压力：多人同时超时，最终计数值仍守恒，可再次全部获准。"""
    preset_parallel(3)
    sid, name = 9, "m3"
    n = 3

    held = [await im.acquire_slot(sid, name, timeout=1) for _ in range(n)]
    assert held == [True] * n

    # 大量并发请求全部超上限 → 各自快速超时返回 False
    async def _try_acquire():
        return await im.acquire_slot(sid, name, timeout=0.02)

    results = await asyncio.gather(*[_try_acquire() for _ in range(10)])
    assert all(r is False for r in results)

    # 释放所有持有，等所有回收任务跑完
    for _ in range(n):
        im.release_slot(sid)
    await asyncio.sleep(0.3)

    g = im._slot_guard[sid]
    assert g._value == n  # 3 个 permit 全部归还

    # 再次并发抢 slot，应全部成功（没有泄漏导致的假满）
    ok = await asyncio.gather(*[im.acquire_slot(sid, name, timeout=0.5) for _ in range(n)])
    assert ok == [True] * n
    for _ in range(n):
        im.release_slot(sid)


# ======================================== 2. slot_limit 反映 preset ========
async def test_slot_limit_reflects_preset_parallel(preset_parallel):
    preset_parallel(4)
    assert im.slot_limit("m") == 4
    assert im._slot_guard_for(1, "m") is not None  # 惰性创建不抛错


async def test_slot_limit_defaults_to_one(preset_parallel):
    """parallel 缺失/空/0 → 回退到 1。"""
    for bad in (None, "", 0):
        preset_parallel(bad)
        assert im.slot_limit("m") == 1, f"parallel={bad!r} 应为 1"


async def test_slot_limit_accepts_string_parallel(preset_parallel):
    preset_parallel("2")  # DB 可能返回字符串
    assert im.slot_limit("m") == 2


# =================================== 3. release_slot 幂等 / 空闸静默 =======
async def test_release_slot_empty_guard_silent():
    """闸不存在（未创建）时 release 不抛错。"""
    im.release_slot(999)
    im.release_slot(123)  # 连续多次也静默


async def test_release_slot_frees_permit(preset_parallel):
    preset_parallel(2)
    sid, name = 11, "m2"
    await im.acquire_slot(sid, name, timeout=1)
    g = im._slot_guard[sid]
    assert g._value == 1  # 占用 1
    im.release_slot(sid)
    assert g._value == 2  # 归还回到满值


# =============================================== 4. stalled 推理活性判定 =====
async def test_stalled_after_timeout_without_success(monkeypatch, fake_clock):
    """begin_request 后超时无 mark_success → 判 stalled。"""
    monkeypatch.setattr(im, "STALL_TRAFFIC_WINDOW", 100.0)
    monkeypatch.setattr(im, "STALL_NO_SUCCESS_SEC", 10.0)
    sid = 13

    im.begin_request(sid)  # t=0
    assert im.stalled_sids() == []  # 刚启动，尚无卡死

    fake_clock.advance(15)  # 有流量但 15s 无成功 → 卡死
    assert im.stalled_sids() == [sid]


async def test_mark_success_clears_stalled(monkeypatch, fake_clock):
    monkeypatch.setattr(im, "STALL_TRAFFIC_WINDOW", 100.0)
    monkeypatch.setattr(im, "STALL_NO_SUCCESS_SEC", 10.0)
    sid = 14
    im.begin_request(sid)
    fake_clock.advance(15)
    assert im.stalled_sids() == [sid]

    im.mark_success(sid)  # t=15
    assert im.stalled_sids() == []  # 成功刷新 → 脱卡

    fake_clock.advance(6)  # t=21 → 距成功 6s < 10，仍正常
    assert im.stalled_sids() == []

    fake_clock.advance(6)  # t=27 → 距成功 12s > 10，且仍有流量 → 再卡死
    assert im.stalled_sids() == [sid]


async def test_idle_instance_not_stalled(monkeypatch, fake_clock):
    """没有请求流量（空闲）的实例即使长期无成功也不判 stalled。"""
    monkeypatch.setattr(im, "STALL_TRAFFIC_WINDOW", 100.0)
    monkeypatch.setattr(im, "STALL_NO_SUCCESS_SEC", 10.0)
    idle_sid = 15
    fake_clock.advance(200)  # 长时间无任何请求
    assert idle_sid not in im.stalled_sids()
    assert im.stalled_sids() == []


async def test_stall_info_snapshot(monkeypatch, fake_clock):
    monkeypatch.setattr(im, "STALL_TRAFFIC_WINDOW", 100.0)
    monkeypatch.setattr(im, "STALL_NO_SUCCESS_SEC", 10.0)
    sid = 16
    im.begin_request(sid)
    fake_clock.advance(5)
    info = im.stall_info(sid)
    assert abs(info["last_request_ago_s"] - 5) <= 0.1
    assert info["last_success_ago_s"] is None  # 尚无成功记录

    im.mark_success(sid)
    fake_clock.advance(5)
    info2 = im.stall_info(sid)
    assert abs(info2["last_success_ago_s"] - 5) <= 0.1


# ============================== 5. stale slot 超时回收 =====================
async def test_force_release_stale_slots(monkeypatch, fake_clock, preset_parallel):
    """reaper：释放“假超时”持有的 permit，幂等，且归还后可再 acquire。"""
    preset_parallel(1)
    sid, name = 17, "m1"

    assert await im.acquire_slot(sid, name, timeout=1) is True  # held_since=t0
    assert im.stale_slot_sids(max_hold_sec=5) == []

    fake_clock.advance(10)  # 持有超 5s → 判 stale
    assert im.stale_slot_sids(max_hold_sec=5) == [sid]

    released = im.force_release_stale_slots(max_hold_sec=5)
    assert released == [sid]
    g = im._slot_guard[sid]
    assert g._value == 1  # permit 已强制归还

    # 幂等：第二次无 stale 可回收
    assert im.force_release_stale_slots(max_hold_sec=5) == []
    assert im.stale_slot_sids(max_hold_sec=5) == []

    # 归还后可正常再 acquire（没有泄漏）
    assert await im.acquire_slot(sid, name, timeout=1) is True
    im.release_slot(sid)