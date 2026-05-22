"""Compactor 单测：触发、failure 不阻塞（V-5）、保留尾巴（V-2 前置）。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from sanshiliu.context.budget import TokenBudget
from sanshiliu.context.compact import Compactor
from sanshiliu.context.prompts import CompactPrompts
from sanshiliu.engine.session import Session
from sanshiliu.engine.types import ChatMessage
from sanshiliu.foundation.errors import LLMFatalError
from sanshiliu.llm.client import LLMClient
from sanshiliu.llm.stream import StreamResult


def _fake_prompts() -> CompactPrompts:
    return CompactPrompts(
        compact_instruction="<instruction A>",
        microcompact_instruction="<instruction B>",
        prompts_dir=Path("."),
    )


def _session_with_history(n_pairs: int) -> Session:
    s = Session.new(channel="test")
    s.messages[0] = ChatMessage(role="system", content="<persona>")
    for i in range(n_pairs):
        s.add_user(f"user msg {i}")
        s.add_assistant(f"assistant msg {i}")
    return s


@pytest_asyncio.fixture
async def llm_stub() -> Any:
    """构造一个 mock LLMClient；chat 用 AsyncMock 返回 StreamResult。"""
    client = LLMClient(
        api_key="k", base_url="https://api.example.com/v1", model="gpt-4o-mini", db=None,
    )
    client.chat = AsyncMock(return_value=StreamResult(  # type: ignore[method-assign]
        text="一段假摘要：用户喜欢简洁；上次问了 X。",
        stop_reason="stop",
        input_tokens=300,
        output_tokens=80,
        latency_ms=100,
    ))
    yield client
    await client.close()


async def test_compact_too_short_skips(llm_stub: LLMClient) -> None:
    """消息太少（无可压内容）→ 直接 return False，不调 LLM。"""
    budget = TokenBudget(max_tokens=100)
    c = Compactor(llm=llm_stub, prompts=_fake_prompts(), budget=budget)
    s = _session_with_history(1)  # system + 1 pair = 3 messages, < 1 + tail(6) + 2
    assert await c.compact(s) is False
    llm_stub.chat.assert_not_called()  # type: ignore[attr-defined]
    assert budget.compact_count == 0


async def test_compact_replaces_history_keeps_tail(llm_stub: LLMClient) -> None:
    """长历史 → 调 LLM、写 summary、保留尾巴 3 对。"""
    budget = TokenBudget(max_tokens=100)
    c = Compactor(llm=llm_stub, prompts=_fake_prompts(), budget=budget, tail_pairs=3)
    s = _session_with_history(10)  # system + 20 user/assistant
    assert len(s.messages) == 21

    ok = await c.compact(s)
    assert ok is True
    assert s.compact_summary == "一段假摘要：用户喜欢简洁；上次问了 X。"
    # 应剩 system + 3 对尾巴
    assert len(s.messages) == 1 + 3 * 2
    # 尾巴是最后 6 条
    assert s.messages[-1].content == "assistant msg 9"
    assert s.messages[-2].content == "user msg 9"
    assert s.messages[1].content == "user msg 7"
    assert budget.compact_count == 1
    # 触发后窗口估算清零
    assert budget.last_prompt_tokens == 0


async def test_compact_llm_failure_does_not_block(llm_stub: LLMClient) -> None:
    """V-5：LLMError 时 compact 跳过返回 False，session 保持不变。"""
    llm_stub.chat = AsyncMock(side_effect=LLMFatalError("400 bad req"))  # type: ignore[method-assign]
    budget = TokenBudget(max_tokens=100)
    c = Compactor(llm=llm_stub, prompts=_fake_prompts(), budget=budget)
    s = _session_with_history(10)
    msgs_before = list(s.messages)
    ok = await c.compact(s)
    assert ok is False
    assert s.messages == msgs_before
    assert s.compact_summary == ""
    assert budget.compact_count == 0


async def test_compact_empty_summary_skips(llm_stub: LLMClient) -> None:
    """LLM 返空字符串 → 当作失败，不污染 session。"""
    llm_stub.chat = AsyncMock(return_value=StreamResult(  # type: ignore[method-assign]
        text="   ", stop_reason="stop", input_tokens=10, output_tokens=0, latency_ms=50,
    ))
    budget = TokenBudget(max_tokens=100)
    c = Compactor(llm=llm_stub, prompts=_fake_prompts(), budget=budget)
    s = _session_with_history(10)
    msgs_before = list(s.messages)
    ok = await c.compact(s)
    assert ok is False
    assert s.messages == msgs_before
    assert budget.compact_count == 0


def test_unused() -> None:
    """避免 time import 未用。"""
    assert time.time() > 0
