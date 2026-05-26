"""Phase 10 wechat 5s 静默合并 + 多模态 content builder 单测。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from sanshiliu.channels.wechat.bot import _build_batch_content
from sanshiliu.channels.wechat.queue import QueueItem, WechatQueue
from sanshiliu.storage.db import Database

# ────────── _build_batch_content ──────────

def _make_item(
    *, id: int, content: str = "", media: str | None = None, ts: int = 0,
) -> QueueItem:
    return QueueItem(
        id=id, ts=ts, session_id="s", user_id="u", group_id=None,
        content=content, msg_type="text" if media is None else "image",
        media=media,
    )


def test_batch_text_only_returns_str() -> None:
    """全是纯文本 → 拼成 str，与 Phase 1-9 行为兼容。"""
    batch = [_make_item(id=1, content="你好"), _make_item(id=2, content="在吗")]
    out = _build_batch_content(batch)
    assert out == "你好\n在吗"


def test_batch_with_image_returns_multimodal_list() -> None:
    media_json = '[{"type":"image_url","image_url":{"url":"https://x/a.png"}}]'
    batch = [
        _make_item(id=1, content="看图"),
        _make_item(id=2, content="", media=media_json),
    ]
    out = _build_batch_content(batch)
    assert isinstance(out, list)
    # 顺序保留：先 text 后 image
    assert out[0] == {"type": "text", "text": "看图"}
    assert out[1] == {"type": "image_url", "image_url": {"url": "https://x/a.png"}}


def test_batch_image_then_text_preserves_order() -> None:
    media_json = '[{"type":"image_url","image_url":{"url":"u1"}}]'
    batch = [
        _make_item(id=1, content="", media=media_json),
        _make_item(id=2, content="数一下"),
    ]
    out = _build_batch_content(batch)
    assert isinstance(out, list)
    assert out[0]["type"] == "image_url"
    assert out[1]["type"] == "text"


def test_batch_malformed_media_falls_back_to_text() -> None:
    batch = [
        _make_item(id=1, content="问题"),
        _make_item(id=2, content="", media="not-json"),
    ]
    out = _build_batch_content(batch)
    # 唯一有效 part 是 text；不抛
    assert isinstance(out, list)
    assert out == [{"type": "text", "text": "问题"}]


# ────────── WechatQueue.fetch_ready_batch ──────────

@pytest.mark.asyncio
async def test_fetch_ready_batch_in_silent_window_returns_none() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "t.db")
        await db.connect()
        try:
            # 模拟一条刚进队 2 秒的消息；窗口 5 秒
            now_ms = 10_000_000
            await db._execute(
                """INSERT INTO channel_messages (ts, channel, direction, session_id,
                   user_id, group_id, content, msg_type, processed)
                   VALUES (?, 'wechat', 'in', 's1', 'u1', NULL, '你好', 'text', 0)""",
                (now_ms - 2_000,),
            )
            q = WechatQueue(db)
            batch = await q.fetch_ready_batch(merge_window_ms=5_000, now_ms=now_ms)
            assert batch is None
        finally:
            await db.close()


@pytest.mark.asyncio
async def test_fetch_ready_batch_after_silence_returns_all() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "t.db")
        await db.connect()
        try:
            now_ms = 10_000_000
            # 两条同会话消息，最近一条 6 秒前；窗口 5 秒 → 应返回 batch
            await db._execute(
                """INSERT INTO channel_messages (ts, channel, direction, session_id,
                   user_id, group_id, content, msg_type, processed)
                   VALUES (?, 'wechat', 'in', 's1', 'u1', NULL, 'a', 'text', 0)""",
                (now_ms - 10_000,),
            )
            await db._execute(
                """INSERT INTO channel_messages (ts, channel, direction, session_id,
                   user_id, group_id, content, msg_type, processed)
                   VALUES (?, 'wechat', 'in', 's1', 'u1', NULL, 'b', 'text', 0)""",
                (now_ms - 6_000,),
            )
            q = WechatQueue(db)
            batch = await q.fetch_ready_batch(merge_window_ms=5_000, now_ms=now_ms)
            assert batch is not None
            assert [it.content for it in batch] == ["a", "b"]
        finally:
            await db.close()


@pytest.mark.asyncio
async def test_fetch_ready_batch_separates_sessions() -> None:
    """另一个会话的近期消息不应阻塞当前会话的静默判定。"""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "t.db")
        await db.connect()
        try:
            now_ms = 10_000_000
            # s1 静默已过窗口；s2 刚收到
            await db._execute(
                """INSERT INTO channel_messages (ts, channel, direction, session_id,
                   user_id, group_id, content, msg_type, processed)
                   VALUES (?, 'wechat', 'in', 's1', 'u1', NULL, 'a', 'text', 0)""",
                (now_ms - 7_000,),
            )
            await db._execute(
                """INSERT INTO channel_messages (ts, channel, direction, session_id,
                   user_id, group_id, content, msg_type, processed)
                   VALUES (?, 'wechat', 'in', 's2', 'u2', NULL, 'b', 'text', 0)""",
                (now_ms - 500,),
            )
            q = WechatQueue(db)
            batch = await q.fetch_ready_batch(merge_window_ms=5_000, now_ms=now_ms)
            # 最旧 s1 已静默，应返回 s1 的 batch
            assert batch is not None
            assert all(it.session_id == "s1" for it in batch)
        finally:
            await db.close()


@pytest.mark.asyncio
async def test_fetch_ready_batch_with_media_column() -> None:
    """带 media 列的消息应保留 media 字段。"""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "t.db")
        await db.connect()
        try:
            now_ms = 10_000_000
            media_json = '[{"type":"image_url","image_url":{"url":"https://x/a.png"}}]'
            await db._execute(
                """INSERT INTO channel_messages (ts, channel, direction, session_id,
                   user_id, group_id, content, msg_type, media, processed)
                   VALUES (?, 'wechat', 'in', 's1', 'u1', NULL, '', 'image', ?, 0)""",
                (now_ms - 6_000, media_json),
            )
            q = WechatQueue(db)
            batch = await q.fetch_ready_batch(merge_window_ms=5_000, now_ms=now_ms)
            assert batch is not None
            assert batch[0].has_media()
            assert "image_url" in (batch[0].media or "")
        finally:
            await db.close()
