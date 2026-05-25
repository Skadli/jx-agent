"""sqlite 队列；webhook 写入 channel_messages.processed=0，bot 拉取后置 1，进程重启天然续跑。"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass

from sanshiliu.foundation.logging import get_logger
from sanshiliu.storage.db import Database

_logger = get_logger(__name__)


@dataclass(frozen=True)
class QueueItem:
    id: int
    ts: int
    session_id: str
    user_id: str
    group_id: str | None
    content: str
    msg_type: str


class WechatQueue:
    """轮询拉取未处理消息；按 id 顺序消费，提供 mark_done / mark_failed。"""

    def __init__(self, db: Database, *, poll_interval: float = 0.5) -> None:
        self._db = db
        self._poll_interval = poll_interval

    async def fetch_next(
        self, *, exclude_ids: set[int] | None = None,
    ) -> QueueItem | None:
        """拉一条最旧的未处理消息；exclude_ids 中的不返回（用来跳过当前 in-flight 的）。

        SQL 直接做排除是必要的：consume_loop 并发派发 handle_one 后，那条消息的
        processed 仍是 0（mark_done 在 handle_one 收尾才写库），如果 fetch_next 还
        拿同一条，新到的审批回复永远捞不到 → 死锁。
        """
        if exclude_ids:
            placeholders = ",".join("?" for _ in exclude_ids)
            cur = await self._db._execute(
                f"""
                SELECT id, ts, session_id, user_id, group_id, content, msg_type
                FROM channel_messages
                WHERE channel = 'wechat' AND direction = 'in' AND processed = 0
                  AND id NOT IN ({placeholders})
                ORDER BY id ASC LIMIT 1
                """,
                tuple(exclude_ids),
            )
        else:
            cur = await self._db._execute(
                """
                SELECT id, ts, session_id, user_id, group_id, content, msg_type
                FROM channel_messages
                WHERE channel = 'wechat' AND direction = 'in' AND processed = 0
                ORDER BY id ASC LIMIT 1
                """,
            )
        row = cur.fetchone()
        if row is None:
            return None
        return QueueItem(
            id=int(row["id"]),
            ts=int(row["ts"]),
            session_id=str(row["session_id"]),
            user_id=str(row["user_id"]),
            group_id=row["group_id"],
            content=str(row["content"]),
            msg_type=str(row["msg_type"]),
        )

    async def mark_done(self, item_id: int, llm_call_id: int | None = None) -> None:
        await self._db._execute(
            "UPDATE channel_messages SET processed = 1, llm_call_id = ? WHERE id = ?",
            (llm_call_id, item_id),
        )

    async def mark_failed(self, item_id: int, reason: str) -> None:
        """标记失败；processed = 2 区别于成功，便于事后人工排查。"""
        await self._db._execute(
            "UPDATE channel_messages SET processed = 2 WHERE id = ?",
            (item_id,),
        )
        _logger.error("wechat 消息处理失败", item_id=item_id, reason=reason)

    async def record_outbound(
        self,
        *,
        session_id: str,
        user_id: str,
        group_id: str | None,
        content: str,
        llm_call_id: int | None,
    ) -> None:
        """落出站消息；direction='out'；用于审计 + 防回声。"""
        await self._db._execute(
            """
            INSERT INTO channel_messages
              (ts, channel, direction, session_id, user_id, group_id, content, msg_type, processed, llm_call_id)
            VALUES (?, 'wechat', 'out', ?, ?, ?, ?, 'text', 1, ?)
            """,
            (int(time.time() * 1000), session_id, user_id, group_id, content, llm_call_id),
        )

    async def wait_until_stop(self, stop_event: asyncio.Event) -> None:
        """便利：等到 stop_event 设置或 poll_interval 触发；外部 poll 循环用。"""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=self._poll_interval)
