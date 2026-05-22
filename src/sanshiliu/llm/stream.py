"""流式响应数据类。

把 openai SDK 的 ChatCompletionChunk 折叠成更紧凑的事件，方便上层 channel 处理。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StreamDelta:
    """单次流增量。Phase 1 只用 text；tool_calls 在 Phase 5 接入。"""

    text: str = ""
    # Phase 5 用：tool_calls 累积；保留字段不引入 Optional 链
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
