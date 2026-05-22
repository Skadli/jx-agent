"""引擎层公共类型；Phase 1 定义 ChatMessage 和 Role。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ChatMessage:
    """OpenAI 兼容的对话消息。"""

    role: Role
    content: str
    # Phase 5 工具调用字段，Phase 1 保持默认
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    # DeepSeek thinking mode 适配：reasoning_content 需原样回传，否则 400
    reasoning_content: str | None = None

    def to_openai(self) -> dict[str, Any]:
        """转 OpenAI chat.completions messages 中的一条。"""
        msg: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.reasoning_content:
            msg["reasoning_content"] = self.reasoning_content
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        if self.name:
            msg["name"] = self.name
        return msg
