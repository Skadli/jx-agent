"""全量上下文压缩；用 LLM 把旧消息 → 摘要，写入 Session.compact_summary。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sanshiliu.context.budget import TokenBudget
from sanshiliu.context.prompts import CompactPrompts
from sanshiliu.engine.types import ChatMessage
from sanshiliu.foundation.errors import LLMError
from sanshiliu.foundation.logging import get_logger
from sanshiliu.llm.client import LLMClient
from sanshiliu.llm.router import LLMRouter

if TYPE_CHECKING:
    from sanshiliu.engine.session import Session

_logger = get_logger(__name__)

# 保留最近 N 对 user/assistant 不压缩；prd 8-V 中 compact 后下一轮仍能引用前文，靠这个尾巴
_DEFAULT_TAIL_PAIRS = 3


def _serialize_history(messages: list[ChatMessage]) -> str:
    """把待压缩消息拼成一段纯文本喂给 LLM；只取 role+content，丢 tool_calls。

    Phase 10：content 是 list[dict] 多模态时，只提取 text part；图片在摘要里不可见，
    LLM 看到的就是文本描述（一般用户附图前都会先说"看图"等文字）。
    """
    lines: list[str] = []
    for m in messages:
        text = m.text_only()
        if not text:
            continue
        lines.append(f"[{m.role}] {text}")
    return "\n\n".join(lines)


def _wrap_content(prior: str, body: str, *, dropped: bool = False) -> str:
    """拼 compact 的 user 输入：有旧摘要就【已有摘要】+【新增对话】累积，否则只给正文。"""
    if not prior:
        return body
    tag = "新增对话（已丢弃更早部分）" if dropped else "新增对话"
    return f"【已有摘要】\n{prior}\n\n【{tag}】\n{body}"


class Compactor:
    """全量压缩器；持有 LLM 客户端、prompts、budget 引用。"""

    def __init__(
        self,
        llm: LLMClient | LLMRouter,
        prompts: CompactPrompts,
        budget: TokenBudget,
        *,
        tail_pairs: int = _DEFAULT_TAIL_PAIRS,
    ) -> None:
        self._llm = llm
        self._prompts = prompts
        self._budget = budget
        self._tail_pairs = tail_pairs

    async def _compact_once(self, session: Session, user_content: str) -> str | None:
        """单次 compact LLM 调用；失败（LLMError）或空摘要返回 None，由调用方决定重试/熔断。"""
        prompt_messages = [
            {"role": "system", "content": self._prompts.compact_instruction},
            {"role": "user", "content": user_content},
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
            _logger.warning("compact 调 LLM 失败", session_id=session.session_id, error=str(exc))
            return None
        summary = result.text.strip()
        if not summary:
            _logger.warning("compact 返回空摘要", session_id=session.session_id)
            return None
        return summary

    async def compact(self, session: Session) -> bool:
        """对 session 执行一次全量压缩；返回 True 表示真的压了，False 表示太短或失败跳过。"""
        tail_keep = self._tail_pairs * 2
        # 至少要有 system + tail + 2 条待压缩消息才有意义
        if len(session.messages) < 1 + tail_keep + 2:
            return False

        # 待压缩 = 去掉 system（msgs[0]）和尾巴
        if tail_keep > 0:
            # 从 -tail_keep 处向前回退到最近一个 user 消息作切点，
            # 保证 tool 消息永远和它前面的 assistant.tool_calls 同侧，避免孤儿 tool_result 触发 400
            cut = len(session.messages) - tail_keep
            while cut > 1 and session.messages[cut].role != "user":
                cut -= 1
            to_compact = session.messages[1:cut]
            if not to_compact:
                return False  # 无法安全切出待压段（尾部全是未结束的 tool 序列等），本轮跳过
            kept_tail = session.messages[cut:]
        else:
            # tail_keep == 0 退化分支：无尾巴；cut=len 会让 messages[cut] 越界，故保持旧行为不走 snap
            to_compact = session.messages[1:]
            if not to_compact:
                return False
            kept_tail = []

        history_text = _serialize_history(to_compact)
        if not history_text.strip():
            return False

        # 把已有摘要折进 LLM 输入，让模型在旧摘要基础上累积合并，避免多轮 compact 渐进性丢史
        prior = session.compact_summary.strip()
        new_summary = await self._compact_once(session, _wrap_content(prior, history_text))
        if new_summary is None:
            # C2：重试一次，丢掉最老的一半待压历史（对齐 CC truncateHeadForPTLRetry）——失败常因
            # 待压历史本身太长撑爆 compact 调用的上下文，砍头再试往往能成。
            half = history_text[len(history_text) // 2:]
            new_summary = await self._compact_once(
                session, _wrap_content(prior, half, dropped=True)
            )
        if new_summary is None:
            # 两次都失败：记熔断计数（连续 3 次后 manager 暂停 compact），不阻塞主对话
            self._budget.note_compact_failure()
            _logger.warning(
                "compact 两次均失败，记熔断计数并跳过本次压缩",
                session_id=session.session_id,
                consecutive_failures=self._budget.consecutive_compact_failures,
            )
            return False

        # 写回 session：替换历史为 system + 尾巴；摘要存到 compact_summary 字段
        # kept_tail 已在前面按 user 边界 snap 算好，这里直接用，勿再按 -tail_keep 重切
        session.compact_summary = new_summary
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
