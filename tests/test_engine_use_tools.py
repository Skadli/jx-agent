"""引擎 complete_turn 的 use_tools 开关单测（方案 A·A1）。

被测不变量：
- use_tools=False → 无论 tools_enabled 如何，传给 LLM 的 tools 必为 None（成长 phase-1 传记零工具）。
- use_tools 默认 True → 有工具就挂工具（非 growth 通道字节不变）。

不打真 LLM/工具：用记录 tools 入参的最小 StubLLM + 鸭子类型的 registry/dispatcher 桩。
风格对齐 tests/test_growth_runner.py 的最小桩写法。
"""

from __future__ import annotations

from typing import Any

import pytest

from sanshiliu.engine.loop import ConversationEngine
from sanshiliu.engine.session import Session
from sanshiliu.llm.stream import StreamResult

# 一个可识别的工具列表 sentinel——挂上工具时 to_openai_tools() 返回它，便于断言"挂没挂"
_TOOLS_SENTINEL: list[dict[str, Any]] = [{"type": "function", "function": {"name": "x"}}]


class StubLLM:
    """记录每次 chat 收到的 tools 入参；不返 tool_calls（让循环一轮即结束）。"""

    def __init__(self) -> None:
        self.tools_seen: list[Any] = []

    async def chat(self, *, tools: Any = None, **_kwargs: Any) -> StreamResult:
        self.tools_seen.append(tools)
        return StreamResult(
            text="生成的内容", stop_reason="stop", input_tokens=1, output_tokens=1,
            latency_ms=1, tool_calls=[],
        )


class StubRegistry:
    """非空工具注册表桩——tools_enabled 据此为真；to_openai_tools 返回 sentinel。"""

    is_empty = False

    def to_openai_tools(self) -> list[dict[str, Any]]:
        return _TOOLS_SENTINEL


class StubDispatcher:
    """占位 dispatcher——本测无 tool_calls，不会被真正调用。"""


def _engine_with_tools(llm: StubLLM) -> ConversationEngine:
    return ConversationEngine(
        llm=llm,  # type: ignore[arg-type]  鸭子类型测试桩
        tool_registry=StubRegistry(),  # type: ignore[arg-type]
        tool_dispatcher=StubDispatcher(),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_use_tools_false_passes_no_tools_even_when_enabled() -> None:
    # 即便 tools_enabled 为真，use_tools=False 也必须给 LLM 传 tools=None（phase-1 零工具）
    llm = StubLLM()
    engine = _engine_with_tools(llm)
    assert engine.tools_enabled is True  # 桩确实让工具开着

    await engine.complete_turn(Session.new(channel="growth"), "产传记", use_tools=False)

    assert llm.tools_seen == [None]


@pytest.mark.asyncio
async def test_default_use_tools_true_attaches_tools() -> None:
    # 默认（不传 use_tools）= 现行为：有工具就挂工具（非 growth 通道字节不变）
    llm = StubLLM()
    engine = _engine_with_tools(llm)

    await engine.complete_turn(Session.new(channel="web"), "你好")

    assert llm.tools_seen == [_TOOLS_SENTINEL]


@pytest.mark.asyncio
async def test_no_registry_passes_no_tools_regardless() -> None:
    # 没有工具注册表（tools_enabled=False）时，use_tools 默认也是 tools=None（与旧行为一致）
    llm = StubLLM()
    engine = ConversationEngine(llm=llm)  # type: ignore[arg-type]
    assert engine.tools_enabled is False

    await engine.complete_turn(Session.new(channel="web"), "你好")

    assert llm.tools_seen == [None]
