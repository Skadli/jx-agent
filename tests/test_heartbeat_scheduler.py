from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from sanshiliu.scheduler import heartbeat as heartbeat_mod
from sanshiliu.scheduler.heartbeat import HeartbeatScheduler, HeartbeatTask, _next_fire_at


async def _noop(_ctx: dict[str, Any]) -> None:
    return None


def test_interval_next_fire_uses_stable_anchor_for_never_run_task(
    monkeypatch: Any,
) -> None:
    task = HeartbeatTask(
        name="interval",
        description="test interval task",
        on_due=_noop,
        interval_seconds=60,
    )
    task._schedule_anchor_at = 1_000.0

    monkeypatch.setattr(heartbeat_mod.time, "time", lambda: 5_000.0)

    assert _next_fire_at(task) == 1_060.0


def test_update_interval_config_refreshes_first_run_anchor(monkeypatch: Any) -> None:
    scheduler = HeartbeatScheduler()
    task = HeartbeatTask(
        name="interval",
        description="test interval task",
        on_due=_noop,
        interval_seconds=60,
    )
    task._schedule_anchor_at = 1_000.0
    scheduler.register(task)

    monkeypatch.setattr(heartbeat_mod.time, "time", lambda: 2_000.0)

    ok, reason = scheduler.update_config("interval", {"interval_seconds": 120})

    assert ok, reason
    assert task._schedule_anchor_at == 2_000.0
    assert _next_fire_at(task) == 2_120.0


def test_daily_next_fire_uses_stable_anchor_for_never_run_task(monkeypatch: Any) -> None:
    """首轮 daily 不能随 tick 过点后重算到明天，否则永远错过第一次触发。"""

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return cls(2026, 6, 2, 3, 0, 30, tzinfo=tz)

    task = HeartbeatTask(
        name="daily",
        description="test daily task",
        on_due=_noop,
        daily_at_hour=3,
    )
    task._schedule_anchor_at = datetime(2026, 6, 2, 2, 59, 30).timestamp()

    monkeypatch.setattr(heartbeat_mod, "datetime", FakeDateTime)

    assert _next_fire_at(task) == datetime(2026, 6, 2, 3, 0, 0).timestamp()


def test_set_enabled_refreshes_first_run_anchor(monkeypatch: Any) -> None:
    scheduler = HeartbeatScheduler()
    task = HeartbeatTask(
        name="daily",
        description="test daily task",
        on_due=_noop,
        enabled=False,
        daily_at_hour=3,
    )
    task._schedule_anchor_at = 1_000.0
    scheduler.register(task)

    monkeypatch.setattr(heartbeat_mod.time, "time", lambda: 2_000.0)

    assert scheduler.set_enabled("daily", True)
    assert task._schedule_anchor_at == 2_000.0


# ── #1：on_due 三态如实上报（last_status / last_message）+ 不崩 tick ──


def _make_task(on_due: Any) -> HeartbeatTask:
    return HeartbeatTask(name="t", description="d", on_due=on_due)


@pytest.mark.asyncio
async def test_execute_marks_error_on_on_due_raise_without_crashing() -> None:
    # #1：on_due 上抛致命降级 → _execute 标 error + 带真因，且自身不再抛（tick 存活）
    async def _boom(_ctx: dict[str, Any]) -> None:
        raise RuntimeError("降级未推进")

    scheduler = HeartbeatScheduler()
    task = _make_task(_boom)
    scheduler.register(task)

    await scheduler._execute(task)  # 不应抛

    assert task.last_status == "error"
    assert "降级未推进" in task.last_message
    assert task._running is False  # finally 复位


@pytest.mark.asyncio
async def test_execute_ok_uses_result_message_when_set() -> None:
    # #1：成功时 last_message 优先取 ctx["result_message"]（成长用它区分"第 N 章已完成"）
    async def _ok(ctx: dict[str, Any]) -> None:
        ctx["result_message"] = "第 1 章已完成（5-10 岁，10 岁）"

    scheduler = HeartbeatScheduler()
    task = _make_task(_ok)
    scheduler.register(task)

    await scheduler._execute(task)

    assert task.last_status == "ok"
    assert task.last_message == "第 1 章已完成（5-10 岁，10 岁）"


@pytest.mark.asyncio
async def test_execute_ok_falls_back_to_gate_reason() -> None:
    # 不设 result_message（dream 行为）→ 回落 gate_reason，字节不变
    async def _gate() -> tuple[bool, str]:
        return True, "可推进"

    scheduler = HeartbeatScheduler()
    task = HeartbeatTask(name="t", description="d", on_due=_noop, gate=_gate)
    scheduler.register(task)

    await scheduler._execute(task)

    assert task.last_status == "ok"
    assert task.last_message == "可推进"
