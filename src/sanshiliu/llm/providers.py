"""多 LLM 后端注册表；以能力声明换路由灵活性。

Phase 10 引入。每个 ProviderSpec 描述一个 OpenAI 兼容后端（DeepSeek、豆包、GLM、Qwen 等）
含 capabilities 集合 / preferred_for 偏好 / cost_tier 排序键。

router.select() 是纯函数读 spec 选 provider；本模块负责持有 LLMClient 实例和回收。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sanshiliu.foundation.logging import get_logger
from sanshiliu.llm.client import LLMClient
from sanshiliu.storage.db import Database

_logger = get_logger(__name__)

# 能力维度；新增能力时扩此 Literal，路由器 required_capabilities 也要同步识别
Capability = Literal["text", "vision", "tool_calls", "reasoning", "audio"]


@dataclass(frozen=True)
class ProviderSpec:
    """一个后端的能力声明 + 连接信息。

    capabilities 是这家后端**实际支持**的能力全集；preferred_for 是显式偏好，
    任何 required 命中 preferred_for 必走这家（即便其他家也能覆盖）。
    """

    name: str
    api_key: str
    base_url: str
    model: str
    capabilities: frozenset[Capability]
    cost_tier: int = 2
    preferred_for: frozenset[Capability] = field(default_factory=frozenset)

    def covers(self, required: frozenset[Capability]) -> bool:
        """spec 是否覆盖 required 全部能力。"""
        return required <= self.capabilities

    def is_preferred_for(self, required: frozenset[Capability]) -> bool:
        """spec 的 preferred_for 与 required 是否有交集。"""
        return bool(self.preferred_for & required)


class ProviderRegistry:
    """持有多个 LLMClient + 对应 spec；按 name 取，按 list 给路由器迭代。

    所有 client 共用同一个 Database 落 llm_calls 表，base_url 字段区分后端。
    """

    def __init__(
        self,
        specs: list[ProviderSpec],
        *,
        db: Database | None = None,
        timeout: float = 60.0,
    ) -> None:
        if not specs:
            raise ValueError("ProviderRegistry 至少需要一个 ProviderSpec")
        self._specs: dict[str, ProviderSpec] = {}
        self._clients: dict[str, LLMClient] = {}
        for spec in specs:
            if spec.name in self._specs:
                raise ValueError(f"重复的 provider name: {spec.name}")
            self._specs[spec.name] = spec
            self._clients[spec.name] = LLMClient(
                api_key=spec.api_key,
                base_url=spec.base_url,
                model=spec.model,
                db=db,
                timeout=timeout,
            )

    @property
    def names(self) -> list[str]:
        return list(self._specs.keys())

    def specs(self) -> list[ProviderSpec]:
        """返回 spec 列表（迭代顺序 = 注册顺序）；路由器按此排序裁决。"""
        return list(self._specs.values())

    def spec(self, name: str) -> ProviderSpec:
        return self._specs[name]

    def client(self, name: str) -> LLMClient:
        return self._clients[name]

    async def close(self) -> None:
        """逐个关 client；单个失败不阻塞其余。"""
        for name, cli in self._clients.items():
            try:
                await cli.close()
            except Exception as exc:
                _logger.warning("provider close 异常（忽略）", provider=name, error=str(exc))


def build_default_registry(settings: object, db: object | None = None) -> ProviderRegistry:
    """从 Settings 装配多后端 ProviderRegistry——wire/runner/repl 共用同一份注册逻辑。

    default = openai_*（DeepSeek 等纯文本，cost_tier=1）；DOUBAO_API_KEY 配了再加
    豆包视觉后端（preferred_for=vision，cost_tier=2）。注册逻辑必须收敛在一处，否则
    web/repl 各自构造单后端 LLMClient 会导致图片请求绕开 router 直击文本模型。
    """
    specs: list[ProviderSpec] = [
        ProviderSpec(
            name="default",
            api_key=settings.openai_api_key.get_secret_value(),  # type: ignore[attr-defined]
            base_url=settings.openai_base_url,  # type: ignore[attr-defined]
            model=settings.openai_model,  # type: ignore[attr-defined]
            capabilities=frozenset({"text", "tool_calls"}),
            cost_tier=1,
        ),
    ]
    doubao_key = getattr(settings, "doubao_api_key", None)
    if doubao_key is not None:
        specs.append(
            ProviderSpec(
                name="doubao",
                api_key=doubao_key.get_secret_value(),
                base_url=settings.doubao_base_url,  # type: ignore[attr-defined]
                model=settings.doubao_model,  # type: ignore[attr-defined]
                capabilities=frozenset({"text", "vision", "tool_calls"}),
                cost_tier=2,
                preferred_for=frozenset({"vision"}),
            )
        )
        _logger.info(
            "豆包多模态 provider 已注册",
            model=settings.doubao_model,  # type: ignore[attr-defined]
            base_url=settings.doubao_base_url,  # type: ignore[attr-defined]
        )
    else:
        _logger.warning("DOUBAO_API_KEY 未配置；vision 请求会因无 provider 覆盖而 fail-fast")
    return ProviderRegistry(specs, db=db)  # type: ignore[arg-type]
