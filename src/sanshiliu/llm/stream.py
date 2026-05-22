"""流式响应数据类；把 SDK chunk 折叠成 channel 友好的事件。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StreamDelta:
    """单次流增量。Phase 1 只用 text；tool_calls 在 Phase 5 接入。"""

    text: str = ""
    # Phase 5 累积 tool_calls，避免 Optional 链
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class StreamResult:
    """流结束后的完整结果。"""

    text: str
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    latency_ms: int
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    # DeepSeek reasoner 系列在 thinking mode 下会返此字段；需原样回传
    reasoning_content: str = ""
