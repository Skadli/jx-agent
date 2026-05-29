from __future__ import annotations

from typing import Any

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
