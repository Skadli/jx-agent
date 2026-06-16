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
        """扫描 session 中超长的 tool_result 消息，截断并加摘要标记；返回折叠数量。

        跳过"最后一条 assistant 之后"的 tool_result——那是本轮刚产生、模型还没读到的结果
        （如刚调 Skill 拿到的完整正文），folding 它等于模型还没看就被截短、白拿。只折叠已被模型
        至少消费过一次的历史 tool_result（与 Claude Code microcompact 语义一致）。
        """
        msgs = session.messages
        last_assistant = -1
        for i, m in enumerate(msgs):
            if m.role == "assistant":
                last_assistant = i
        folded = 0
        for i, msg in enumerate(msgs):
            if msg.role != "tool" or msg.content is None:
                continue
            if i > last_assistant:
                continue  # 未消费的最新一批 tool_result，别折
            if not isinstance(msg.content, str) or len(msg.content) <= self._max_chars:
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
