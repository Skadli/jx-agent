"""prompt_builder 单测：只调度 PersonaSnapshot，不引入字面 prompt 文本。"""

from __future__ import annotations

import time
from pathlib import Path

from sanshiliu.engine.prompt_builder import build_system_prompt
from sanshiliu.identity.types import PERSONA_FILES, PersonaSnapshot


def _snapshot(content_prefix: str = "SEC") -> PersonaSnapshot:
    sections = {name: f"{content_prefix}-{name}" for name in PERSONA_FILES}
    return PersonaSnapshot(
        sections=sections,
        mtimes={name: time.time() for name in PERSONA_FILES},
        loaded_at=time.time(),
        persona_dir=Path("."),
    )


def test_build_uses_snapshot_only() -> None:
    snap = _snapshot()
    out = build_system_prompt(snap)
    assert all(f"SEC-{name}" in out for name in PERSONA_FILES)


def test_build_no_extra_static_text() -> None:
    """任何输出字符必须可追溯到 snapshot 内容（除结构性分隔符）。"""
    snap = _snapshot("XYZ")
    out = build_system_prompt(snap)
    # 去掉所有 snapshot 段内容后，剩下的应只是分隔符空白
    residual = out
    for v in snap.sections.values():
        residual = residual.replace(v, "")
    residual = residual.strip().replace("---", "").strip()
    assert residual == "", f"prompt 含未来源于 markdown 的额外文本：{residual!r}"
