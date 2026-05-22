"""system prompt 拼装器。

Phase 1：极简——只返回一个占位 system prompt。
Phase 2：拼 persona/*.md。
Phase 6：拼活跃 skills 的 body。
Phase 7：CLAUDE.md（项目级 + 全局）+ memdir top-N 注入到 prompt 顶部。
"""

from __future__ import annotations

_DEFAULT_SYSTEM = (
    "你是「三十六贱笑」的早期占位人格（Phase 1 阶段）。\n"
    "用简洁、口语化的中文回答用户的问题。\n"
    "完整的人设将在 Phase 2 接入。"
)


def build_system_prompt() -> str:
    """构造 system prompt 字符串。Phase 1 仅返回占位文本。"""
    return _DEFAULT_SYSTEM
