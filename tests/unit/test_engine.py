"""引擎层单测：session + loop（Phase 2 更新：system 由 PersonaSnapshot 决定）。"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from sanshiliu.engine.loop import ConversationEngine
from sanshiliu.engine.session import Session
from sanshiliu.identity.types import PERSONA_FILES, PersonaSnapshot
from sanshiliu.llm.client import LLMClient
from sanshiliu.llm.stream import StreamDelta
from sanshiliu.storage.db import Database


def _fake_snapshot(text: str = "PERSONA_OK") -> PersonaSnapshot:
    """构造一个最简快照；内容不重要，重要的是结构。"""
    sections = {name: f"{text}-{name}" for name in PERSONA_FILES}
    mtimes = {name: time.time() for name in PERSONA_FILES}
    return PersonaSnapshot(
        sections=sections,
        mtimes=mtimes,
        loaded_at=time.time(),
        persona_dir=Path("."),
    )


def test_session_new_has_placeholder_system() -> None:
    """Phase 2：Session.new 只放占位 system，内容应为空。"""
    s = Session.new(channel="repl")
    assert len(s.messages) == 1
    assert s.messages[0].role == "system"
    assert s.messages[0].content == ""


def test_session_add_user_assistant_appends() -> None:
    s = Session.new(channel="repl")
    s.add_user("你好")
    s.add_assistant("你也好")
    assert [m.role for m in s.messages] == ["system", "user", "assistant"]


def test_session_refresh_system_prompt_uses_persona() -> None:
    s = Session.new(channel="repl")
    snap = _fake_snapshot()
    s.refresh_system_prompt(snap)
    assert s.messages[0].role == "system"
    assert "root.md" in s.messages[0].content
    assert "PERSONA_OK" in s.messages[0].content


def test_session_to_openai_messages_filters_empty_system() -> None:
    s = Session.new(channel="repl")
    s.add_user("hi")
    msgs = s.to_openai_messages()
    # 占位 system 应被滤掉；只剩 user
    assert len(msgs) == 1
    assert msgs[0] == {"role": "user", "content": "hi"}


def test_session_to_openai_messages_keeps_nonempty_system() -> None:
    s = Session.new(channel="repl")
    s.refresh_system_prompt(_fake_snapshot())
    s.add_user("hi")
    msgs = s.to_openai_messages()
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1] == {"role": "user", "content": "hi"}


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    d = Database(tmp_path / "t.db")
    await d.connect()
    yield d
    await d.close()


async def test_engine_stream_turn_appends_assistant(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = LLMClient(
        api_key="k", base_url="https://api.example.com/v1", model="gpt-4o-mini", db=db,
    )

    async def _stream_chat(*_a: Any, **_kw: Any) -> AsyncIterator[StreamDelta]:
        for ch in "ok":
            yield StreamDelta(text=ch)

    monkeypatch.setattr(client, "stream_chat", _stream_chat)

    # 不挂 loader，验证 Phase 1 兼容路径仍 OK
    engine = ConversationEngine(llm=client, db=db)
    session = Session.new(channel="repl")
    before = len(session.messages)

    deltas: list[str] = []
    async for d in engine.stream_turn(session, "hi"):
        deltas.append(d.text)

    assert "".join(deltas) == "ok"
    assert len(session.messages) == before + 2
    assert session.messages[-1].role == "assistant"
    assert session.messages[-1].content == "ok"
    await client.close()
