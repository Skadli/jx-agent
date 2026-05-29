"""通用心跳调度；周期性 / 每日定点跑注册的 HeartbeatTask 集合。

设计要点：
- **单后台任务**：所有 task 共用一个 asyncio task，60s tick 一次扫所有 task 是否到点。
- **两种调度**：`daily_at_hour`（每日某点）或 `interval_seconds`（周期）；二选一。
- **可选 gate**：触发前再判一道闸门，闸门函数返回 `(passed, reason)`；不通过则记 `gate-failed`。
- **手动触发**：`run_now(name)` 绕过 due 判定（但仍走 gate），用于 dashboard 按钮。
- **状态在内存**：last_run_at / last_status / last_message 都在 HeartbeatTask 字段里；
  运行状态重启后丢失；开关与配置由 persistence.py 持久化到 heartbeat.json。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sanshiliu.foundation.logging import get_logger

_logger = get_logger(__name__)

_TICK_INTERVAL_SEC = 60.0

OnDueCallback = Callable[[dict[str, Any]], Awaitable[None]]
"""task.on_due 签名：传入 ctx dict（含 task_name / last_run_at / gate_reason）→ None。"""

GateResult = tuple[bool, str]
"""(passed, reason)；reason 给 dashboard 显示，无论 passed 与否都要写人话。"""

GateCallback = Callable[[], Awaitable[GateResult]]


@dataclass
class HeartbeatTask:
    """一条心跳任务的全部状态——定义 + 运行时；dashboard 直接渲染这个对象的 to_dict。"""

    name: str
    description: str
    on_due: OnDueCallback
    enabled: bool = True
    # 调度：二选一；都不填 = 永不自动触发（只能手动 run_now）
    daily_at_hour: int | None = None
    interval_seconds: int | None = None
    # 闸门：可选
    gate: GateCallback | None = None
    # task 自定义参数；通过 dashboard PUT /config 可改，gate / on_due 闭包通过 task.extra_params 读取
    # 例：dream task 用 extra_params["min_sessions"] = 3
    extra_params: dict[str, Any] = field(default_factory=dict)
    # 哪些 extra_params 是 dashboard 可编辑的（白名单 + 类型；前端据此生成表单）
    # 形如 {"min_sessions": {"type": "int", "min": 1, "label": "最少新增 session 数"}}
    editable_params: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 运行状态
    last_run_at: float | None = None
    last_status: str = "never-run"
    last_message: str = ""
    last_duration_ms: int | None = None
    # 临时锁：防止 tick 重叠触发同一 task
    _running: bool = field(default=False, repr=False)
    # 周期任务首次触发的稳定基准；不能在 _next_fire_at 每次用 time.time() 重算。
    _schedule_anchor_at: float = field(default_factory=time.time, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "daily_at_hour": self.daily_at_hour,
            "interval_seconds": self.interval_seconds,
            "has_gate": self.gate is not None,
            "extra_params": dict(self.extra_params),
            "editable_params": dict(self.editable_params),
            "last_run_at": self.last_run_at,
            "last_status": self.last_status,
            "last_message": self.last_message,
            "last_duration_ms": self.last_duration_ms,
            "next_fire_at": _next_fire_at(self),
        }


class HeartbeatScheduler:
    """注册 task 后 start() 启动单后台 tick；stop() 优雅退出。"""

    def __init__(self, tick_interval_sec: float = _TICK_INTERVAL_SEC) -> None:
        self._tasks: dict[str, HeartbeatTask] = {}
        self._tick_interval = tick_interval_sec
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        # 持有 fire-and-forget 子任务的引用，防 GC 中途回收（RUF006）
        self._inflight: set[asyncio.Task[None]] = set()
        # 可写状态变更钩子；toggle / update_config 改完调一次；用于触发持久化落盘
        self._on_state_change: Callable[[], None] | None = None

    def set_change_hook(self, hook: Callable[[], None] | None) -> None:
        """注册"配置/开关变更后"回调；持久化层用这个把当前状态 dump 到 JSON。"""
        self._on_state_change = hook

    def _emit_change(self) -> None:
        if self._on_state_change is None:
            return
        try:
            self._on_state_change()
        except Exception as exc:
            _logger.error("heartbeat on_state_change 失败（不阻塞）", error=str(exc))

    def register(self, task: HeartbeatTask) -> None:
        if task.name in self._tasks:
            raise ValueError(f"heartbeat task 重名: {task.name}")
        if task.daily_at_hour is not None and not 0 <= task.daily_at_hour <= 23:
            raise ValueError(f"daily_at_hour 必须 0-23，收到 {task.daily_at_hour}")
        if task.interval_seconds is not None and task.interval_seconds < 1:
            raise ValueError(f"interval_seconds 必须 >= 1，收到 {task.interval_seconds}")
        self._tasks[task.name] = task
        _logger.info(
            "heartbeat 注册 task",
            name=task.name,
            enabled=task.enabled,
            daily_at_hour=task.daily_at_hour,
            interval_seconds=task.interval_seconds,
        )

    def list_tasks(self) -> list[HeartbeatTask]:
        return list(self._tasks.values())

    def get(self, name: str) -> HeartbeatTask | None:
        return self._tasks.get(name)

    def set_enabled(self, name: str, enabled: bool) -> bool:
        task = self._tasks.get(name)
        if task is None:
            return False
        task.enabled = enabled
        _logger.info("heartbeat task 开关", name=name, enabled=enabled)
        self._emit_change()
        return True

    def update_config(self, name: str, patch: dict[str, Any]) -> tuple[bool, str]:
        """dashboard 批量改任务配置。

        允许字段：enabled / daily_at_hour / interval_seconds / extra_params.<editable key>。
        校验失败 → 返 (False, reason)。校验通过 → apply + 触发 change 钩子。
        """
        task = self._tasks.get(name)
        if task is None:
            return False, f"task 不存在: {name}"

        # ── 复制一份做校验，全部通过再 apply ──
        old_enabled = task.enabled
        old_daily = task.daily_at_hour
        old_interval = task.interval_seconds
        new_enabled = task.enabled
        new_daily = task.daily_at_hour
        new_interval = task.interval_seconds
        new_extra = dict(task.extra_params)

        if "enabled" in patch:
            if not isinstance(patch["enabled"], bool):
                return False, "enabled 必须 bool"
            new_enabled = patch["enabled"]

        if "daily_at_hour" in patch:
            v = patch["daily_at_hour"]
            if v is None:
                new_daily = None
            elif isinstance(v, int) and 0 <= v <= 23:
                new_daily = v
            else:
                return False, "daily_at_hour 必须 0-23 或 null"

        if "interval_seconds" in patch:
            v = patch["interval_seconds"]
            if v is None:
                new_interval = None
            elif isinstance(v, int) and v >= 1:
                new_interval = v
            else:
                return False, "interval_seconds 必须 >= 1 或 null"

        if new_daily is not None and new_interval is not None:
            return False, "daily_at_hour 与 interval_seconds 必须二选一"

        if "extra_params" in patch:
            ep = patch["extra_params"]
            if not isinstance(ep, dict):
                return False, "extra_params 必须 dict"
            for k, v in ep.items():
                if k not in task.editable_params:
                    return False, f"extra_params.{k} 不在白名单 (editable_params)"
                spec = task.editable_params[k]
                expect_type = spec.get("type", "str")
                if expect_type == "int":
                    if not isinstance(v, int) or isinstance(v, bool):
                        return False, f"extra_params.{k} 必须 int"
                    if "min" in spec and v < spec["min"]:
                        return False, f"extra_params.{k} 必须 >= {spec['min']}"
                    if "max" in spec and v > spec["max"]:
                        return False, f"extra_params.{k} 必须 <= {spec['max']}"
                elif expect_type == "bool":
                    if not isinstance(v, bool):
                        return False, f"extra_params.{k} 必须 bool"
                elif expect_type == "str":
                    if not isinstance(v, str):
                        return False, f"extra_params.{k} 必须 str"
                new_extra[k] = v

        # ── 校验通过，原子 apply ──
        task.enabled = new_enabled
        task.daily_at_hour = new_daily
        task.interval_seconds = new_interval
        task.extra_params = new_extra
        if (
            old_daily != new_daily
            or old_interval != new_interval
            or (not old_enabled and new_enabled and task.last_run_at is None)
        ):
            task._schedule_anchor_at = time.time()
        _logger.info(
            "heartbeat task 配置更新",
            name=name,
            enabled=new_enabled,
            daily_at_hour=new_daily,
            interval_seconds=new_interval,
            extra_params=new_extra,
        )
        self._emit_change()
        return True, "已更新"

    async def run_now(self, name: str) -> tuple[bool, str]:
        """dashboard 按钮用：绕过 due 判定，但仍走 gate；返回 (started, reason)。

        注意：实际执行是异步的——start 后立刻返回，结果通过 last_status 看。
        """
        task = self._tasks.get(name)
        if task is None:
            return False, f"task 不存在: {name}"
        if task._running:
            return False, "task 正在运行中，请稍后再点"
        sub = asyncio.create_task(self._execute(task, manual=True), name=f"heartbeat-{name}-manual")
        self._inflight.add(sub)
        sub.add_done_callback(self._inflight.discard)
        return True, "已触发"

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="heartbeat-scheduler")
        _logger.info(
            "heartbeat scheduler 启动",
            tasks=len(self._tasks),
            tick_sec=self._tick_interval,
        )

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=self._tick_interval + 2.0)
            except TimeoutError:
                self._task.cancel()
        self._task = None
        _logger.info("heartbeat scheduler 已停止")

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._tick_interval)
                break
            except TimeoutError:
                pass
            now = time.time()
            for task in list(self._tasks.values()):
                if not task.enabled or task._running:
                    continue
                next_fire = _next_fire_at(task)
                if now >= next_fire:
                    # 异步起子任务，不阻塞 tick；同一 task 多次到点也只跑一份（_running 锁）
                    sub = asyncio.create_task(self._execute(task), name=f"heartbeat-{task.name}")
                    self._inflight.add(sub)
                    sub.add_done_callback(self._inflight.discard)

    async def _execute(self, task: HeartbeatTask, *, manual: bool = False) -> None:
        if task._running:
            return
        task._running = True
        task.last_status = "running"
        task.last_message = "手动触发执行中" if manual else "到点执行中"
        start_ms = time.time()
        ctx: dict[str, Any] = {
            "task_name": task.name,
            "manual": manual,
            "last_run_at": task.last_run_at,
            "gate_reason": "",
        }
        try:
            if task.gate is not None:
                try:
                    passed, reason = await task.gate()
                except Exception as exc:
                    task.last_status = "error"
                    task.last_message = f"gate 异常: {exc}"
                    _logger.error("heartbeat gate 异常", name=task.name, error=str(exc))
                    return
                ctx["gate_reason"] = reason
                if not passed:
                    task.last_status = "gate-failed"
                    task.last_message = reason
                    task.last_run_at = time.time()
                    _logger.info("heartbeat 闸门未过", name=task.name, reason=reason)
                    return

            try:
                await task.on_due(ctx)
            except Exception as exc:
                task.last_status = "error"
                task.last_message = f"执行异常: {exc}"
                _logger.error("heartbeat on_due 异常", name=task.name, error=str(exc))
                return

            task.last_status = "ok"
            task.last_message = ctx.get("gate_reason") or "完成"
        finally:
            task.last_run_at = time.time()
            task.last_duration_ms = int((time.time() - start_ms) * 1000)
            task._running = False


def _next_fire_at(task: HeartbeatTask) -> float:
    """根据调度模式 + last_run_at 算下一次应触发的 unix 时间戳；不可调度的 task 返 inf。"""
    if task.interval_seconds is not None:
        base = task.last_run_at if task.last_run_at is not None else task._schedule_anchor_at
        # 首次（last_run_at=None）使用注册/配置更新时间作为基准；
        # 第一轮等满一个 interval 才跑。想立刻跑请用 run_now。
        return base + task.interval_seconds

    if task.daily_at_hour is not None:
        now = datetime.now()
        target_today = now.replace(
            hour=task.daily_at_hour, minute=0, second=0, microsecond=0
        )
        if task.last_run_at is None:
            next_dt = target_today if now < target_today else target_today + timedelta(days=1)
            return next_dt.timestamp()
        last_dt = datetime.fromtimestamp(task.last_run_at)
        target_on_last_day = last_dt.replace(
            hour=task.daily_at_hour, minute=0, second=0, microsecond=0
        )
        next_dt = target_on_last_day
        if last_dt >= target_on_last_day:
            next_dt = target_on_last_day + timedelta(days=1)
        return next_dt.timestamp()

    return float("inf")
