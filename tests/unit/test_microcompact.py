"""MicroCompactor 单测（V-3 前置：tool_result 替换为摘要标记）。"""

from __future__ import annotations

from pathlib import Path

from sanshiliu.context.budget import TokenBudget
from sanshiliu.context.microcompact import MicroCompactor
from sanshiliu.context.prompts import CompactPrompts
from sanshiliu.engine.session import Session
from sanshiliu.engine.types import ChatMessage


def _prompts() -> CompactPrompts:
    return CompactPrompts(compact_instruction="A", microcompact_instruction="B", prompts_dir=Path("."))


def test_short_tool_result_not_folded() -> None:
    budget = TokenBudget(max_tokens=1000)
    mc = MicroCompactor(prompts=_prompts(), budget=budget, max_chars=100)
    s = Session.new(channel="test")
    short = "x" * 50
    s.messages.append(ChatMessage(role="tool", content=short, tool_call_id="t1"))
    n = mc.fold_oversize(s)
    assert n == 0
    assert s.messages[-1].content == short
    assert budget.microcompact_count == 0


def test_long_tool_result_truncated_with_marker() -> None:
    budget = TokenBudget(max_tokens=1000)
    mc = MicroCompactor(prompts=_prompts(), budget=budget, max_chars=100)
    s = Session.new(channel="test")
    long_text = "x" * 500
    s.messages.append(ChatMessage(role="tool", content=long_text, tool_call_id="t1"))
    n = mc.fold_oversize(s)
    assert n == 1
    folded = s.messages[-1].content
    assert folded.startswith("x" * 100)
    assert "microcompact 截断" in folded
    assert "400 字符" in folded  # 500 - 100
    assert budget.microcompact_count == 1


def test_multiple_oversized_all_folded() -> None:
    budget = TokenBudget(max_tokens=1000)
    mc = MicroCompactor(prompts=_prompts(), budget=budget, max_chars=50)
    s = Session.new(channel="test")
    for i in range(3):
        s.messages.append(ChatMessage(role="tool", content="y" * 200, tool_call_id=f"t{i}"))
    n = mc.fold_oversize(s)
    assert n == 3
    assert budget.microcompact_count == 1  # 单次扫描算一次 microcompact 事件


def test_non_tool_messages_untouched() -> None:
    budget = TokenBudget(max_tokens=1000)
    mc = MicroCompactor(prompts=_prompts(), budget=budget, max_chars=10)
    s = Session.new(channel="test")
    s.add_user("用户消息" * 100)  # 远超 10 字符
    s.add_assistant("助手消息" * 100)
    n = mc.fold_oversize(s)
    assert n == 0
    assert len(s.messages[-1].content) > 100
