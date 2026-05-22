"""全量上下文压缩；用 LLM 把旧消息 → 摘要，写入 Session.compact_summary。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sanshiliu.context.budget import TokenBudget
from sanshiliu.context.prompts import CompactPrompts
from sanshiliu.engine.types import ChatMessage
from sanshiliu.foundation.errors import LLMError
from sanshiliu.foundation.logging import get_logger
from sanshiliu.llm.client import LLMClient

if TYPE_CHECKING:
    from sanshiliu.engine.session import Session

_logger = get_logger(__name__)

# 保留最近 N 对 user/assistant 不压缩；prd 8-V 中 compact 后下一轮仍能引用前文，靠这个尾巴
_DEFAULT_TAIL_PAIRS = 3


def _serialize_history(messages: list[ChatMessage]) -> str:
    """把待压缩消息拼成一段纯文本喂给 LLM；只取 role+content，丢 tool_calls。"""
    lines: list[str] = []
    for m in messages:
        if not m.content:
            continue
        lines.append(f"[{m.role}] {m.content}")
    return "\n\n".join(lines)


class Compactor:
    """全量压缩器；持有 LLM 客户端、prompts、budget 引用。"""

    def __init__(
        self,
        llm: LLMClient,
        prompts: CompactPrompts,
        budget: TokenBudget,
        *,
        tail_pairs: int = _DEFAULT_TAIL_PAIRS,
    ) -> None:
        self._llm = llm
        self._prompts = prompts
        self._budget = budget
        self._tail_pairs = tail_pairs

    async def compact(self, session: Session) -> bool:
        """对 session 执行一次全量压缩；返回 True 表示真的压了，False 表示太短或失败跳过。"""
        tail_keep = self._tail_pairs * 2
        # 至少要有 system + tail + 2 条待压缩消息才有意义
        if len(session.messages) < 1 + tail_keep + 2:
            return False

        # 待压缩 = 去掉 system（msgs[0]）和尾巴
        to_compact = session.messages[1:-tail_keep] if tail_keep > 0 else session.messages[1:]
        if not to_compact:
            return False

        history_text = _serialize_history(to_compact)
        if not history_text.strip():
            return False

        prompt_messages = [
            {"role": "system", "content": self._prompts.compact_instruction},
            {"role": "user", "content": history_text},
        ]

        try:
            result = await self._llm.chat(
                messages=prompt_messages,
                session_id=session.session_id,
                channel="compact-internal",
                user_id=session.user_id,
                temperature=0.3,
            )
        except LLMError as exc:
            # V-5：compact 失败不阻塞主对话
            _logger.warning(
                "compact 调 LLM 失败，跳过本次压缩",
                session_id=session.session_id,
                error=str(exc),
            )
            return False

        new_summary = result.text.strip()
        if not new_summary:
            _logger.warning("compact 返回空摘要，跳过", session_id=session.session_id)
            return False

        # 写回 session：替换历史为 system + 尾巴；摘要存到 compact_summary 字段
        session.compact_summary = new_summary
        kept_tail = session.messages[-tail_keep:] if tail_keep > 0 else []
        session.messages = [session.messages[0], *kept_tail]

        self._budget.note_compact()
        _logger.info(
            "compact 完成",
            session_id=session.session_id,
            summary_chars=len(new_summary),
            kept_tail=len(kept_tail),
            compact_count=self._budget.compact_count,
        )
        return True
