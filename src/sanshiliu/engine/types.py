"""引擎层公共类型；Phase 1 定义 ChatMessage 和 Role，Phase 10 起 content 支持多模态。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]

# Phase 10：OpenAI 多模态格式——content 可以是 str 或 [{type, ...}, ...]
# 例：[{"type":"text","text":"看图"}, {"type":"image_url","image_url":{"url":"data:..."}}]
MessageContent = str | list[dict[str, Any]]


@dataclass
class ChatMessage:
    """OpenAI 兼容的对话消息；content 支持多模态。"""

    role: Role
    content: MessageContent
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

    def text_only(self) -> str:
        """提取纯文本：str 直接返；list 拼接所有 text part；其余 part 忽略。

        用于 memory_extractor / 命令分发等只关心文字的场景。
        """
        if isinstance(self.content, str):
            return self.content
        out: list[str] = []
        for part in self.content:
            if isinstance(part, dict) and part.get("type") == "text":
                t = part.get("text")
                if isinstance(t, str):
                    out.append(t)
        return " ".join(out)
