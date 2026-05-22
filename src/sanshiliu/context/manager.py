"""上下文管理器；协调 Session/Budget/Compact 三者，对 engine 暴露统一接口。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sanshiliu.context.budget import TokenBudget
from sanshiliu.context.compact import Compactor
from sanshiliu.context.microcompact import MicroCompactor
from sanshiliu.context.prompts import CompactPrompts
from sanshiliu.foundation.logging import get_logger
from sanshiliu.llm.client import LLMClient

if TYPE_CHECKING:
    from sanshiliu.engine.session import Session

_logger = get_logger(__name__)


class ContextManager:
    """每会话一个；engine 在每轮前调 ensure_within_budget，调用后调 record_usage。"""

    def __init__(
        self,
        *,
        llm: LLMClient,
        prompts: CompactPrompts,
        max_context_tokens: int,
        compact_threshold_ratio: float = 0.8,
    ) -> None:
        self._budget = TokenBudget(
            max_tokens=max_context_tokens,
            compact_threshold_ratio=compact_threshold_ratio,
        )
        self._compactor = Compactor(llm=llm, prompts=prompts, budget=self._budget)
        self._microcompactor = MicroCompactor(prompts=prompts, budget=self._budget)

    @property
    def budget(self) -> TokenBudget:
        return self._budget

    def record_usage(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_read: int = 0,
        cache_create: int = 0,
    ) -> None:
        """每次 LLM 调用结束后由 engine 调用，刷新 budget。"""
        self._budget.update_from_usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read=cache_read,
            cache_create=cache_create,
        )

    async def ensure_within_budget(self, session: Session) -> bool:
        """每轮前调用；超阈值则触发 compact。返回是否真的压了。"""
        # 先尝试 microcompact 折叠长 tool_result（成本低，先做）
        self._microcompactor.fold_oversize(session)

        if not self._budget.should_compact():
            return False

        _logger.info(
            "命中 compact 阈值，开始压缩",
            session_id=session.session_id,
            last_prompt_tokens=self._budget.last_prompt_tokens,
            threshold=self._budget.threshold,
        )
        return await self._compactor.compact(session)

    def stats(self) -> dict[str, int | float]:
        """REPL /stats 命令读取的数据；返回新 dict 避免外部修改。"""
        return dict(self._budget.stats())
