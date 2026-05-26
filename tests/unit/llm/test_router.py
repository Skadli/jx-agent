"""Phase 10 router 单测；覆盖 4 场景：纯文本 / 仅图片 / 文图混合 / 多 provider 优先级。"""

from __future__ import annotations

import pytest

from sanshiliu.foundation.errors import LLMFatalError
from sanshiliu.llm.providers import ProviderSpec
from sanshiliu.llm.router import required_capabilities, select

# ────────── fixture：典型 provider 组合 ──────────

def _default_text() -> ProviderSpec:
    return ProviderSpec(
        name="default", api_key="sk-deepseek", base_url="https://api.deepseek.com",
        model="deepseek-chat",
        capabilities=frozenset({"text", "tool_calls"}),
        cost_tier=1,
    )


def _doubao_vision() -> ProviderSpec:
    return ProviderSpec(
        name="doubao", api_key="sk-doubao",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        model="doubao-seed-2-0-pro-260215",
        capabilities=frozenset({"text", "vision", "tool_calls"}),
        cost_tier=2,
        preferred_for=frozenset({"vision"}),
    )


def _glm_vision_cheap() -> ProviderSpec:
    """假想：比豆包便宜的 vision provider，用来验 cost_tier 排序仅在无 preferred_for 命中时生效。"""
    return ProviderSpec(
        name="glm", api_key="sk-glm", base_url="https://open.bigmodel.cn/api/paas/v4",
        model="glm-4v-plus",
        capabilities=frozenset({"text", "vision"}),
        cost_tier=1,
        # 不声明 preferred_for；同等候选时只比 cost_tier
    )


# ────────── required_capabilities ──────────

def test_required_text_only() -> None:
    """纯文本只需 text。"""
    msgs = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "嗨"},
    ]
    assert required_capabilities(msgs) == frozenset({"text"})


def test_required_with_image() -> None:
    """list content 中含 image_url part → 加 vision。"""
    msgs = [
        {"role": "user", "content": [
            {"type": "text", "text": "这是什么？"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,xxx"}},
        ]},
    ]
    assert required_capabilities(msgs) == frozenset({"text", "vision"})


def test_required_tool_calls_history() -> None:
    """历史里 assistant 用过 tool_calls → 需 tool_calls 能力（保持后端一致）。"""
    msgs = [
        {"role": "user", "content": "查天气"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "1", "type": "function", "function": {"name": "weather", "arguments": "{}"}},
        ]},
    ]
    caps = required_capabilities(msgs)
    assert "tool_calls" in caps
    assert "text" in caps


def test_required_image_and_tool() -> None:
    """图片 + tool 历史 → 需 vision + tool_calls 双能力。"""
    msgs = [
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,yyy"}},
            {"type": "text", "text": "数一下"},
        ]},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "1", "type": "function", "function": {"name": "count", "arguments": "{}"}},
        ]},
    ]
    assert required_capabilities(msgs) == frozenset({"text", "vision", "tool_calls"})


def test_required_audio() -> None:
    """input_audio part → 加 audio。"""
    msgs = [
        {"role": "user", "content": [
            {"type": "input_audio", "input_audio": {"data": "xxx", "format": "wav"}},
        ]},
    ]
    assert "audio" in required_capabilities(msgs)


# ────────── select ──────────

def test_select_text_picks_cheapest_default() -> None:
    """纯文本：default(cost=1) 优于 doubao(cost=2)。"""
    providers = [_doubao_vision(), _default_text()]  # 故意倒序，验排序稳定
    chosen = select(frozenset({"text"}), providers)
    assert chosen.name == "default"


def test_select_vision_picks_doubao_via_preferred() -> None:
    """vision：豆包 preferred_for 命中 → 必走豆包，即便 GLM 更便宜。"""
    providers = [_default_text(), _glm_vision_cheap(), _doubao_vision()]
    chosen = select(frozenset({"text", "vision"}), providers)
    assert chosen.name == "doubao"


def test_select_vision_no_preferred_uses_cost_tier() -> None:
    """vision：去掉豆包 preferred_for 后，GLM(cost=1) 应胜过 doubao(cost=2)。"""
    doubao_no_pref = ProviderSpec(
        name="doubao", api_key="x", base_url="https://x", model="m",
        capabilities=frozenset({"text", "vision"}),
        cost_tier=2,
    )
    providers = [_default_text(), _glm_vision_cheap(), doubao_no_pref]
    chosen = select(frozenset({"text", "vision"}), providers)
    assert chosen.name == "glm"


def test_select_raises_when_no_coverage() -> None:
    """无任何 provider 覆盖 vision → fail-fast。"""
    providers = [_default_text()]
    with pytest.raises(LLMFatalError, match="no provider covers required caps"):
        select(frozenset({"text", "vision"}), providers)


def test_select_tool_calls_with_vision_must_intersect() -> None:
    """同时需 vision + tool_calls：只有同时覆盖两者的 provider 进入候选。"""
    providers = [_default_text(), _glm_vision_cheap(), _doubao_vision()]
    chosen = select(frozenset({"text", "vision", "tool_calls"}), providers)
    # GLM 不支持 tool_calls，default 不支持 vision；只剩豆包
    assert chosen.name == "doubao"
