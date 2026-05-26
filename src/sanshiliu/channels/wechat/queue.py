"""sqlite 队列；webhook 写入 channel_messages.processed=0，bot 拉取后置 1，进程重启天然续跑。

Phase 10：增 fetch_ready_batch 支持 N ms 静默窗口合并；用于把"先文字后图片"合并为
单次多模态 LLM 调用。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from typing import Any

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
    # Phase 10：JSON 串，存 [{"type":"image_url","image_url":{"url":...}}, ...]；None=纯文本
    media: str | None = None

    def has_media(self) -> bool:
        return bool(self.media)


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
                SELECT id, ts, session_id, user_id, group_id, content, msg_type, media
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
                SELECT id, ts, session_id, user_id, group_id, content, msg_type, media
                FROM channel_messages
                WHERE channel = 'wechat' AND direction = 'in' AND processed = 0
                ORDER BY id ASC LIMIT 1
                """,
            )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_item(row)

    async def fetch_ready_batch(
        self,
        *,
        merge_window_ms: int,
        merge_window_media_ms: int | None = None,
        exclude_ids: set[int] | None = None,
        now_ms: int | None = None,
    ) -> list[QueueItem] | None:
        """Phase 10：找一个会话的全部未处理消息——前提是最近一条入站消息距今 ≥ window。

        - 完整 batch（纯文本 / 图文齐备 / 视频文件 / 任何含文字的组合）：merge_window_ms
          默认 0 → 下个 poll 周期立即触发 LLM
        - 仅图未配文字：merge_window_media_ms 默认 5s → 给用户打 caption 的时间；
          5s 内文字到了自动切回短窗口立即合并发送。
          原因：用户发图后切到输入框打字描述常需 5~10s，零等待会把图和后续描述切成
          两个 batch，AI 自然回复两次。

        返回 None 表示：队列空，或最旧消息所在会话仍在静默窗口内（再等等）。
        进程重启时自然兜底：积压消息超过窗口会被一次性 flush。
        """
        # 先按 fetch_next 的口径取一条最旧 unprocessed，决定要看哪个 session
        oldest = await self.fetch_next(exclude_ids=exclude_ids)
        if oldest is None:
            return None

        ref_now = now_ms if now_ms is not None else int(time.time() * 1000)

        # 同时拿 max(ts)、media 计数、纯文字行计数。
        # 长窗口只在"有图 + 还没文字跟进"时启用——典型的"用户发图等着打 caption"。
        # 一旦文字也到了（或本来就只有文字），用短窗口尽快回复，不让用户干等。
        cur = await self._db._execute(
            """
            SELECT
              MAX(ts) AS latest,
              SUM(CASE WHEN media IS NOT NULL AND media != '' THEN 1 ELSE 0 END) AS media_count,
              SUM(CASE WHEN media IS NULL OR media = '' THEN 1 ELSE 0 END) AS text_count
            FROM channel_messages
            WHERE channel = 'wechat' AND direction = 'in' AND processed = 0
              AND session_id = ?
            """,
            (oldest.session_id,),
        )
        row = cur.fetchone()
        latest_ts = int(row["latest"]) if row and row["latest"] is not None else oldest.ts
        has_media = bool(row and (row["media_count"] or 0) > 0)
        has_text_followup = bool(row and (row["text_count"] or 0) > 0)
        # 仅当"图已到但还没文字"时用长窗口；其余三种组合（文+图 / 仅文 / 多图无文）都用短
        waiting_for_caption = (
            has_media and not has_text_followup and merge_window_media_ms is not None
        )
        effective_window = merge_window_media_ms if waiting_for_caption else merge_window_ms
        if ref_now - latest_ts < effective_window:
            return None  # 仍在静默窗口，等下个 poll

        # 静默：把该会话所有 unprocessed 一次性拉出来
        if exclude_ids:
            placeholders = ",".join("?" for _ in exclude_ids)
            cur = await self._db._execute(
                f"""
                SELECT id, ts, session_id, user_id, group_id, content, msg_type, media
                FROM channel_messages
                WHERE channel = 'wechat' AND direction = 'in' AND processed = 0
                  AND session_id = ?
                  AND id NOT IN ({placeholders})
                ORDER BY id ASC
                """,
                (oldest.session_id, *exclude_ids),
            )
        else:
            cur = await self._db._execute(
                """
                SELECT id, ts, session_id, user_id, group_id, content, msg_type, media
                FROM channel_messages
                WHERE channel = 'wechat' AND direction = 'in' AND processed = 0
                  AND session_id = ?
                ORDER BY id ASC
                """,
                (oldest.session_id,),
            )
        rows = cur.fetchall()
        items = [_row_to_item(r) for r in rows]
        return items if items else None

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


def _row_to_item(row: Any) -> QueueItem:
    """Row → QueueItem；老库 media 列可能缺失（迁移前），用 .get 风格容错。"""
    try:
        media = row["media"]
    except (IndexError, KeyError):
        media = None
    return QueueItem(
        id=int(row["id"]),
        ts=int(row["ts"]),
        session_id=str(row["session_id"]),
        user_id=str(row["user_id"]),
        group_id=row["group_id"],
        content=str(row["content"]),
        msg_type=str(row["msg_type"]),
        media=media if media else None,
    )
