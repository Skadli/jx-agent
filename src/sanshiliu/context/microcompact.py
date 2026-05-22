"""工具结果折叠 microcompact；Phase 5 才接 tool_calls，Phase 3 只提供契约 + 简化版。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sanshiliu.context.budget import TokenBudget
from sanshiliu.context.prompts import CompactPrompts
from sanshiliu.foundation.logging import get_logger

if TYPE_CHECKING:
    from sanshiliu.engine.session import Session

_logger = get_logger(__name__)

# 单条 tool_result 内容超过此字符数才折叠；目前用截断兜底，Phase 5+ 才调 LLM
_DEFAULT_MAX_TOOL_RESULT_CHARS = 2000


class MicroCompactor:
    """tool_result 折叠器；Phase 3 不调 LLM，仅做长度截断 + 加标记。"""

    def __init__(
        self,
        prompts: CompactPrompts,
        budget: TokenBudget,
        *,
        max_chars: int = _DEFAULT_MAX_TOOL_RESULT_CHARS,
    ) -> None:
        self._prompts = prompts
        self._budget = budget
        self._max_chars = max_chars

    def fold_oversize(self, session: Session) -> int:
        """扫描 session 中超长的 tool_result 消息，截断并加摘要标记；返回折叠数量。"""
        folded = 0
        for msg in session.messages:
            if msg.role != "tool" or msg.content is None:
                continue
            if len(msg.content) <= self._max_chars:
                continue
            truncated = msg.content[: self._max_chars]
            msg.content = (
                f"{truncated}\n\n[... tool_result 被 microcompact 截断 "
                f"{len(msg.content) - self._max_chars} 字符；完整内容已落 jsonl 日志 ...]"
            )
            folded += 1
        if folded > 0:
            self._budget.note_microcompact()
            _logger.info(
                "microcompact 完成", session_id=session.session_id, folded=folded
            )
        return folded
