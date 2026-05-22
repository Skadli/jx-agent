"""wechat 限流单测（V-5：31 条/日 → 第 31 条收冷却提示）。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio

from sanshiliu.channels.wechat.rate_limit import WechatRateLimiter
from sanshiliu.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    d = Database(tmp_path / "t.db")
    await d.connect()
    yield d
    await d.close()


async def test_user_quota_30_per_day_then_block(db: Database) -> None:
    """V-5：30 条放行，第 31 条 user_quota 拒绝。"""
    rl = WechatRateLimiter(db, per_user_per_day=30, global_per_minute=10_000)
    for i in range(30):
        d = await rl.take("w1")
        assert d.allowed, f"第 {i+1} 条应放行"
    d = await rl.take("w1")
    assert d.allowed is False
    assert d.reason == "user_quota"


async def test_user_quotas_are_per_wxid(db: Database) -> None:
    rl = WechatRateLimiter(db, per_user_per_day=2, global_per_minute=10_000)
    assert (await rl.take("w1")).allowed
    assert (await rl.take("w1")).allowed
    assert (await rl.take("w1")).allowed is False
    # 别的 wxid 不受影响
    assert (await rl.take("w2")).allowed


async def test_global_rate_per_minute(db: Database) -> None:
    rl = WechatRateLimiter(db, per_user_per_day=1_000, global_per_minute=2)
    assert (await rl.take("w1")).allowed
    assert (await rl.take("w2")).allowed
    d = await rl.take("w3")
    assert d.allowed is False
    assert d.reason == "global_rps"
