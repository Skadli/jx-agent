"""会话状态：消息历史 + 元数据。

Phase 1：纯内存历史；Phase 3 接入 context.manager 时会改造为持久化版本。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sanshiliu.engine.prompt_builder import build_system_prompt
from sanshiliu.engine.types import ChatMessage


@dataclass
class Session:
    """单个会话。

    通常由 channel 创建并维持生命周期；多 channel 共享同一 engine，但 session 各自独立。
    """

    session_id: str
    channel: str
    user_id: str | None = None
    created_at: float = field(default_factory=time.time)
    messages: list[ChatMessage] = field(default_factory=list)

    def __post_init__(self) -> None:
        # 启动即注入 system prompt；Phase 2 之后 system 会随 persona 热重载而更新
        if not self.messages:
            self.messages.append(ChatMessage(role="system", content=build_system_prompt()))

    @classmethod
    def new(cls, channel: str, user_id: str | None = None) -> Session:
        """新建会话，session_id 自动用 uuid4。"""
        return cls(
            session_id=str(uuid.uuid4()),
            channel=channel,
            user_id=user_id,
        )

    def add_user(self, text: str) -> ChatMessage:
        msg = ChatMessage(role="user", content=text)
        self.messages.append(msg)
        return msg

    def add_assistant(self, text: str) -> ChatMessage:
        msg = ChatMessage(role="assistant", content=text)
        self.messages.append(msg)
        return msg

    def to_openai_messages(self) -> list[dict[str, Any]]:
        """导出供 LLMClient 使用的 messages。"""
        return [m.to_openai() for m in self.messages]

    def refresh_system_prompt(self) -> None:
        """重新生成 system 消息（Phase 2 热重载时调用）。"""
        if self.messages and self.messages[0].role == "system":
            self.messages[0] = ChatMessage(role="system", content=build_system_prompt())
        else:
            self.messages.insert(0, ChatMessage(role="system", content=build_system_prompt()))
