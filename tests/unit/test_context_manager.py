"""ContextManager 集成单测；锁住 stats 契约（REPL /stats 依赖）。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest_asyncio

from sanshiliu.context.manager import ContextManager
from sanshiliu.context.prompts import CompactPrompts
from sanshiliu.engine.session import Session
from sanshiliu.engine.types import ChatMessage
from sanshiliu.llm.client import LLMClient
from sanshiliu.llm.stream import StreamResult


def _prompts() -> CompactPrompts:
    return CompactPrompts(compact_instruction="A", microcompact_instruction="B", prompts_dir=Path("."))


@pytest_asyncio.fixture
async def llm_stub() -> LLMClient:
    c = LLMClient(
        api_key="k", base_url="https://api.example.com/v1", model="gpt-4o-mini", db=None,
    )
    c.chat = AsyncMock(return_value=StreamResult(  # type: ignore[method-assign]
        text="假摘要", stop_reason="stop", input_tokens=200, output_tokens=50, latency_ms=10,
    ))
    yield c
    await c.close()


async def test_ensure_within_budget_no_compact_when_under_threshold(llm_stub: LLMClient) -> None:
    cm = ContextManager(
        llm=llm_stub, prompts=_prompts(), max_context_tokens=10_000, compact_threshold_ratio=0.8,
    )
    cm.record_usage(input_tokens=1_000, output_tokens=50)  # 远低于阈值 8000
    s = Session.new(channel="t")
    s.messages[0] = ChatMessage(role="system", content="<sys>")
    for _ in range(5):
        s.add_user("u")
        s.add_assistant("a")
    triggered = await cm.ensure_within_budget(s)
    assert triggered is False
    llm_stub.chat.assert_not_called()  # type: ignore[attr-defined]


async def test_ensure_within_budget_triggers_compact_when_over_threshold(llm_stub: LLMClient) -> None:
    cm = ContextManager(
        llm=llm_stub, prompts=_prompts(), max_context_tokens=1_000, compact_threshold_ratio=0.8,
    )
    cm.record_usage(input_tokens=900, output_tokens=50)  # 超阈值 800
    s = Session.new(channel="t")
    s.messages[0] = ChatMessage(role="system", content="<sys>")
    for i in range(10):
        s.add_user(f"u{i}")
        s.add_assistant(f"a{i}")
    triggered = await cm.ensure_within_budget(s)
    assert triggered is True
    assert cm.budget.compact_count == 1


async def test_stats_dict_is_copy(llm_stub: LLMClient) -> None:
    """stats() 返回新 dict——外部修改不应影响内部状态。"""
    cm = ContextManager(llm=llm_stub, prompts=_prompts(), max_context_tokens=100)
    s1 = cm.stats()
    s1["compact_count"] = 999  # 篡改
    s2 = cm.stats()
    assert s2["compact_count"] == 0


async def test_microcompact_runs_before_compact(llm_stub: LLMClient) -> None:
    """ensure_within_budget 应先 microcompact，再 compact。"""
    cm = ContextManager(
        llm=llm_stub, prompts=_prompts(), max_context_tokens=1_000, compact_threshold_ratio=0.8,
    )
    cm.record_usage(input_tokens=900, output_tokens=10)
    s = Session.new(channel="t")
    s.messages[0] = ChatMessage(role="system", content="<sys>")
    # 加一个超长 tool_result + 足够的对话历史
    s.messages.append(ChatMessage(role="tool", content="z" * 5000, tool_call_id="t1"))
    for i in range(10):
        s.add_user(f"u{i}")
        s.add_assistant(f"a{i}")
    await cm.ensure_within_budget(s)
    assert cm.budget.microcompact_count == 1
    assert cm.budget.compact_count == 1
