"""会话列表（/api/sessions 的数据源）不应混入成长/做梦的内部合成会话。

list_recent_sessions 不带 channel 过滤时（dashboard“全部”视图）要剔除 growth/scheduler；
但显式按 channel 取仍能单独拿到，供成长视图 / 做梦记录各自排障。
"""

from __future__ import annotations

from pathlib import Path

from sanshiliu.storage.db import Database


async def test_list_recent_sessions_hides_internal_channels(tmp_path: Path) -> None:
    db = Database(tmp_path / "sessions.db")
    await db.connect()
    try:
        for sid, channel in (
            ("s-web", "web"),
            ("s-repl", "repl"),
            ("s-wechat", "wechat"),
            ("s-growth", "growth"),  # 成长逐章
            ("s-dream", "scheduler"),  # 做梦反思
        ):
            await db.upsert_session(session_id=sid, channel=channel, user_id=channel)

        rows = await db.list_recent_sessions(limit=50)
        ids = {r["id"] for r in rows}
        assert ids == {"s-web", "s-repl", "s-wechat"}
        assert "s-growth" not in ids and "s-dream" not in ids

        # 显式按内部 channel 取仍可拿到（不被默认列表的剔除波及）
        only_growth = await db.list_recent_sessions(limit=50, channel="growth")
        assert [r["id"] for r in only_growth] == ["s-growth"]
    finally:
        await db.close()
