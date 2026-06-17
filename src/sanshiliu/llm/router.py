"""能力声明式 LLM 路由器；按 messages 内容挑 provider。

设计原则：
- 纯函数 select()，无会话状态；每轮 LLM 调用都独立挑选
- preferred_for 命中优先于 cost_tier；同优先级里 cost_tier 升序
- 缺能力覆盖时 fail-fast 抛 LLMFatalError，不要悄悄回退（避免对方说"不支持 vision"）
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sanshiliu.foundation.errors import LLMFatalError
from sanshiliu.foundation.logging import get_logger
from sanshiliu.llm.providers import Capability, ProviderRegistry, ProviderSpec
from sanshiliu.llm.stream import StreamDelta, StreamResult

_logger = get_logger(__name__)


def required_capabilities(messages: list[dict[str, Any]]) -> frozenset[Capability]:
    """扫 messages 推断本轮需要的能力集合。

    text 总是 baseline；命中 image_url / input_audio 加 vision/audio；
    任一消息含 tool_calls 字段 → 需 tool_calls；reasoning_content 字段 → 需 reasoning。
    """
    caps: set[Capability] = {"text"}
    for msg in messages:
        # tool_calls 字段（assistant 历史 / system 注入的 tool list 都算）
        if msg.get("tool_calls"):
            caps.add("tool_calls")
        if msg.get("reasoning_content"):
            caps.add("reasoning")
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype in ("image_url", "image", "video_url"):
                caps.add("vision")
            elif ptype in ("input_audio", "audio"):
                caps.add("audio")
    return frozenset(caps)


def select(
    required: frozenset[Capability],
    providers: list[ProviderSpec],
) -> ProviderSpec:
    """从 providers 中挑一个满足 required 的；preferred_for 命中优先，再按 cost_tier 升序。

    失败抛 LLMFatalError，不静默回退——回退会让用户看到"不支持 vision"的下游报错，
    更糟糕。
    """
    candidates = [p for p in providers if p.covers(required)]
    if not candidates:
        capset = ",".join(sorted(required))
        names = ",".join(p.name for p in providers) or "(empty)"
        raise LLMFatalError(
            f"no provider covers required caps: {capset}; available: {names}"
        )

    # 排序键：preferred 命中 → 0，否则 1；其次按 cost_tier；最后按 name 保稳定
    candidates.sort(
        key=lambda p: (
            0 if p.is_preferred_for(required) else 1,
            p.cost_tier,
            p.name,
        )
    )
    return candidates[0]


class LLMRouter:
    """对 engine 暴露的统一入口；签名与 LLMClient 保持一致以便 engine.loop 一行切换。

    底层仍是 LLMClient；本层只负责"挑 provider → 委托调用"。
    """

    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    @property
    def registry(self) -> ProviderRegistry:
        return self._registry

    def select_for(self, messages: list[dict[str, Any]]) -> ProviderSpec:
        """暴露给 engine.loop 用——在调用前决定走哪家，方便日志追溯。"""
        req = required_capabilities(messages)
        spec = select(req, self._registry.specs())
        _logger.debug(
            "router 选 provider",
            provider=spec.name, model=spec.model,
            required=sorted(req),
        )
        return spec

    @property
    def model(self) -> str:
        """兼容 LLMClient.model 接口；返回 default provider 的 model 名（仅观测用）。"""
        return self._registry.specs()[0].model

    @property
    def base_url(self) -> str:
        return self._registry.specs()[0].base_url

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        session_id: str,
        channel: str,
        user_id: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        max_retries_on_disconnect: int = 2,
    ) -> AsyncIterator[StreamDelta]:
        spec = self.select_for(messages)
        client = self._registry.client(spec.name)
        async for delta in client.stream_chat(
            messages,
            session_id=session_id,
            channel=channel,
            user_id=user_id,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            max_retries_on_disconnect=max_retries_on_disconnect,
        ):
            yield delta

    async def stream_chat_collect(
        self,
        messages: list[dict[str, Any]],
        *,
        session_id: str,
        channel: str,
        user_id: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        max_retries_on_disconnect: int = 2,
        result_sink: list[StreamResult],
    ) -> AsyncIterator[StreamDelta]:
        spec = self.select_for(messages)
        client = self._registry.client(spec.name)
        async for delta in client.stream_chat_collect(
            messages,
            session_id=session_id,
            channel=channel,
            user_id=user_id,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            max_retries_on_disconnect=max_retries_on_disconnect,
            result_sink=result_sink,
        ):
            yield delta

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        session_id: str,
        channel: str,
        user_id: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> StreamResult:
        spec = self.select_for(messages)
        client = self._registry.client(spec.name)
        return await client.chat(
            messages,
            session_id=session_id,
            channel=channel,
            user_id=user_id,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def close(self) -> None:
        await self._registry.close()
