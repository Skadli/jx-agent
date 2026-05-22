"""Phase 3 改造：Session.compact_summary 写到 system 末尾 + to_openai_messages 行为。"""

from __future__ import annotations

import time
from pathlib import Path

from sanshiliu.engine.session import Session
from sanshiliu.identity.types import PERSONA_FILES, PersonaSnapshot


def _snap() -> PersonaSnapshot:
    return PersonaSnapshot(
        sections={n: f"<{n}>" for n in PERSONA_FILES},
        mtimes={n: time.time() for n in PERSONA_FILES},
        loaded_at=time.time(),
        persona_dir=Path("."),
    )


def test_empty_persona_empty_summary_no_system_emitted() -> None:
    s = Session.new(channel="t")
    s.add_user("hi")
    out = s.to_openai_messages()
    assert out == [{"role": "user", "content": "hi"}]


def test_persona_only_emits_system() -> None:
    s = Session.new(channel="t")
    s.refresh_system_prompt(_snap())
    s.add_user("hi")
    out = s.to_openai_messages()
    assert out[0]["role"] == "system"
    assert "<root.md>" in out[0]["content"]
    assert out[1] == {"role": "user", "content": "hi"}


def test_summary_only_still_emits_system() -> None:
    s = Session.new(channel="t")
    s.compact_summary = "<某段摘要>"
    s.add_user("hi")
    out = s.to_openai_messages()
    assert out[0]["role"] == "system"
    assert out[0]["content"] == "<某段摘要>"


def test_persona_plus_summary_concatenated() -> None:
    s = Session.new(channel="t")
    s.refresh_system_prompt(_snap())
    s.compact_summary = "<摘要 X>"
    s.add_user("hi")
    out = s.to_openai_messages()
    content = out[0]["content"]
    assert "<root.md>" in content
    assert "<摘要 X>" in content
    # 分隔符位置：persona 在前、摘要在后
    assert content.index("<root.md>") < content.index("<摘要 X>")


def test_refresh_persona_does_not_clear_summary() -> None:
    s = Session.new(channel="t")
    s.compact_summary = "<重要摘要>"
    s.refresh_system_prompt(_snap())
    assert s.compact_summary == "<重要摘要>"
    out = s.to_openai_messages()
    assert "<重要摘要>" in out[0]["content"]
