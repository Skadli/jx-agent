"""把"做梦"包装成一个 HeartbeatTask。

闸门（gate）：扫 <memdir>/*dream-*.md 最近 mtime，数 sessions/*.jsonl mtime 大于它的文件数；
              达到 min_sessions（来自 task.extra_params，dashboard 可改）→ 通过，否则 gate-failed。
触发（on_due）：调用 DreamRunner 跨通道拼材料 + 跑 engine 一轮做梦。

注意：min_sessions / daily_at_hour 都从 task 对象上读（不是闭包参数），这样 dashboard
PUT /config 后立刻生效，无需重启。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from sanshiliu.scheduler.dream_runner import DreamRunner
from sanshiliu.scheduler.heartbeat import GateResult, HeartbeatTask

if TYPE_CHECKING:
    from sanshiliu.engine.loop import ConversationEngine
    from sanshiliu.storage.db import Database

# 默认值——首次启动 seed；后续被 heartbeat.json 覆盖
_DEFAULT_FIRE_HOUR = 3
_DEFAULT_MIN_SESSIONS = 3


def build_dream_task(
    *,
    engine: ConversationEngine,
    db: Database | None,
    sessions_dir: Path,
    memdir_dir: Path,
    fire_hour: int = _DEFAULT_FIRE_HOUR,
    min_sessions: int = _DEFAULT_MIN_SESSIONS,
    enabled: bool = False,
    dream_log_path: Path | None = None,
) -> HeartbeatTask:
    # memdir_dir：做梦产物落点（diff 记本次写入的记忆）；dream_log_path：做梦历史日志落点
    # （每次 ok/skipped/error 追加一条，供心跳页回看）。二者透传给 DreamRunner。
    runner = DreamRunner(
        engine=engine,
        db=db,
        sessions_dir=sessions_dir,
        memdir_dir=memdir_dir,
        dream_log_path=dream_log_path,
    )

    # task 需要被闭包引用——但 task 对象在 return 之前还没构造好。
    # 解决：用一个 mutable 容器（list）当占位，build 完后把 task 放进去；闭包读 box[0]。
    box: list[HeartbeatTask | None] = [None]

    async def gate() -> GateResult:
        t = box[0]
        assert t is not None
        threshold = int(t.extra_params.get("min_sessions", _DEFAULT_MIN_SESSIONS))
        last_ts = _last_dream_mtime(memdir_dir)
        count = _count_sessions_since(sessions_dir, last_ts)
        if count < threshold:
            return False, f"新增 session {count} < 阈值 {threshold}"
        return True, f"新增 session {count} >= 阈值 {threshold}"

    async def on_due(ctx: dict[str, Any]) -> None:
        last_ts = _last_dream_mtime(memdir_dir)
        count = _count_sessions_since(sessions_dir, last_ts)
        # runner 返回人话结果（完成/跳过/失败）→ 写进 ctx 让 heartbeat 标到 last_message，
        # 心跳页那行不再是笼统"完成"；完整历史另在 dream-log（DreamRunner 内已落）。
        ctx["result_message"] = await runner(count, last_ts)

    task = HeartbeatTask(
        name="dream",
        description="跨通道收集近期对话素材，按 dream skill 协议做一次梦，写入 memdir。",
        on_due=on_due,
        enabled=enabled,
        daily_at_hour=fire_hour,
        gate=gate,
        extra_params={"min_sessions": min_sessions},
        editable_params={
            "min_sessions": {
                "type": "int",
                "min": 1,
                "max": 100,
                "label": "最少新增 session 数",
                "hint": "距上次做梦后累积多少个新对话文件才放行",
            },
        },
    )
    box[0] = task
    return task


def _last_dream_mtime(memdir_dir: Path) -> float:
    if not memdir_dir.is_dir():
        return 0.0
    latest = 0.0
    # SaveMemory 落盘文件名为 `{type}_{name}_{ts}.md`，做梦记录 type=reference、name=dream-*，
    # 实际文件名形如 `reference_dream-2026-05-27-xxx_1716.md`，故用 `*dream-*.md` 匹配。
    for f in memdir_dir.glob("*dream-*.md"):
        try:
            ts = f.stat().st_mtime
        except OSError:
            continue
        if ts > latest:
            latest = ts
    return latest


def _count_sessions_since(sessions_dir: Path, since_ts: float) -> int:
    if not sessions_dir.is_dir():
        return 0
    count = 0
    for f in sessions_dir.glob("*.jsonl"):
        try:
            if f.stat().st_mtime > since_ts:
                count += 1
        except OSError:
            continue
    return count
