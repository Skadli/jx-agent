"""system prompt 拼装器；任何 prompt 文本必须来自 persona/*.md，本文件不含字面 prompt。"""

from __future__ import annotations

from sanshiliu.identity.types import PersonaSnapshot


def build_system_prompt(persona: PersonaSnapshot) -> str:
    """直接交给 PersonaSnapshot 拼装；本函数只负责调度，不引入新文本。"""
    return persona.to_system_prompt()
