"""引擎层公共类型。

Phase 1 极简：只定义 ChatMessage 和 Role。
后续 phase 加 tool_call / tool_result / system_block 等。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ChatMessage:
    """对话消息。

    保持 OpenAI 格式兼容：to_openai() 直接喂给 chat.completions。
    """

    role: Role
    content: str
    # Phase 5 用：tool_call 相关字段；Phase 1 留默认
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    def to_openai(self) -> dict[str, Any]:
        """转 OpenAI chat.completions messages 中的一条。"""
        msg: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        if self.name:
            msg["name"] = self.name
        return msg
