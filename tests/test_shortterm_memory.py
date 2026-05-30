from pathlib import Path

import pytest

from sanshiliu.engine.session import Session
from sanshiliu.engine.types import ChatMessage
from sanshiliu.memory.shortterm import ShortTermMemory


@pytest.mark.asyncio
async def test_reload_uses_latest_snapshot_as_full_frame(tmp_path: Path) -> None:
    memory = ShortTermMemory(tmp_path)
    sid = "session-1"

    await memory.append_message(sid, ChatMessage(role="user", content="old"))
    session = Session(session_id=sid, channel="web")
    session.add_user("new")
    session.add_assistant("answer")
    await memory.snapshot(session)
    await memory.append_message(sid, ChatMessage(role="user", content="after"))

    messages = await memory.reload(sid)

    assert [(m.role, m.content) for m in messages] == [
        ("user", "new"),
        ("assistant", "answer"),
        ("user", "after"),
    ]


@pytest.mark.asyncio
async def test_snapshot_does_not_persist_system_messages(tmp_path: Path) -> None:
    memory = ShortTermMemory(tmp_path)
    session = Session(session_id="session-2", channel="web")
    session.refresh_system_prompt(type("P", (), {"to_system_prompt": lambda self: "system"})())
    session.add_user("hi")

    await memory.snapshot(session)

    rows = await memory.writer.read_all(session.session_id)
    roles = [m["role"] for m in rows[-1]["messages"]]
    assert roles == ["user"]
