"""wechat queue 单测（V-6：重启恢复未处理消息）。"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio

from sanshiliu.channels.wechat.queue import WechatQueue
from sanshiliu.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    d = Database(tmp_path / "t.db")
    await d.connect()
    yield d
    await d.close()


async def _seed(db: Database, n: int) -> list[int]:
    ids: list[int] = []
    for i in range(n):
        cur = await db._execute(  # noqa: SLF001
            """
            INSERT INTO channel_messages (ts, channel, direction, session_id, user_id, content, msg_type, processed)
            VALUES (?, 'wechat', 'in', 'sess', 'w1', ?, 'text', 0)
            """,
            (int(time.time() * 1000) + i, f"msg-{i}"),
        )
        ids.append(int(cur.lastrowid or 0))
    return ids


async def test_fetch_next_returns_oldest(db: Database) -> None:
    ids = await _seed(db, 3)
    q = WechatQueue(db)
    item = await q.fetch_next()
    assert item is not None
    assert item.id == ids[0]
    assert item.content == "msg-0"


async def test_fetch_next_empty_returns_none(db: Database) -> None:
    q = WechatQueue(db)
    assert await q.fetch_next() is None


async def test_mark_done_excludes_from_future_fetches(db: Database) -> None:
    ids = await _seed(db, 2)
    q = WechatQueue(db)
    item = await q.fetch_next()
    assert item is not None and item.id == ids[0]
    await q.mark_done(item.id)
    next_item = await q.fetch_next()
    assert next_item is not None and next_item.id == ids[1]


async def test_restart_recovery_keeps_unprocessed(db: Database) -> None:
    """V-6：模拟 kill -9 → 重启；processed=0 的消息还在。"""
    ids = await _seed(db, 3)
    q = WechatQueue(db)
    # 假设处理一半就崩了
    first = await q.fetch_next()
    assert first is not None
    await q.mark_done(first.id)

    # 模拟"重启"：新建 queue 实例
    q2 = WechatQueue(db)
    remaining = []
    while True:
        item = await q2.fetch_next()
        if item is None:
            break
        remaining.append(item.id)
        await q2.mark_done(item.id)
    assert remaining == ids[1:]


async def test_mark_failed_sets_processed_2(db: Database) -> None:
    ids = await _seed(db, 1)
    q = WechatQueue(db)
    await q.mark_failed(ids[0], "test")
    cur = await db._execute(  # noqa: SLF001
        "SELECT processed FROM channel_messages WHERE id = ?", (ids[0],)
    )
    assert cur.fetchone()["processed"] == 2


async def test_record_outbound_writes_out_row(db: Database) -> None:
    q = WechatQueue(db)
    await q.record_outbound(
        session_id="sess", user_id="w1", group_id=None, content="reply", llm_call_id=None,
    )
    cur = await db._execute(  # noqa: SLF001
        "SELECT direction, content, processed FROM channel_messages WHERE direction='out'"
    )
    row = cur.fetchone()
    assert row["direction"] == "out"
    assert row["content"] == "reply"
    assert row["processed"] == 1
