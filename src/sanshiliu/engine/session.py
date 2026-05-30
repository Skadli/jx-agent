"""会话状态；Phase 2 起 system 由 PersonaLoader 决定，Phase 3 起加 compact_summary 字段。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sanshiliu.engine.prompt_builder import build_system_prompt
from sanshiliu.engine.types import ChatMessage, MessageContent
from sanshiliu.identity.types import PersonaSnapshot

# 注入 compact 摘要时与 persona 之间的结构性分隔；纯胶水非 prompt 内容
_SUMMARY_JOINER = "\n\n---\n\n"

# 贴在 system prompt 最末尾的"近因锚点"。LLM 对结尾最敏感，而 style.md 的长度/禁 markdown
# 硬约束在 persona 中段、后面还压着 modules/skills/compact，容易被"信息型问题→列清单"的默认
# 反射盖过。这里只做一次行为复述：不带字数（权威约束仍在 style.md，避免双源漂移）、不扫用户
# 输入（长短仍由模型自己判断），纯粹把"短 + 禁 markdown"挪到模型最听得进去的位置。
_REPLY_LENGTH_ANCHOR = (
    "（发送前自检）这是微信聊天，不是写文档：默认一两句话直出；"
    "不用 markdown —— 不要编号列表 1./2./3.、不要 - 列表、不要 **加粗**、不要标题；"
    "只有用户这轮明确要展开 / 要方案 / 要脚本时才长，否则先短答或追问。"
)


@dataclass
class Session:
    """由 channel 创建并维护的独立会话；system 消息位置在 [0]。"""

    session_id: str
    channel: str
    user_id: str | None = None
    created_at: float = field(default_factory=time.time)
    messages: list[ChatMessage] = field(default_factory=list)
    # Phase 3 起：上下文压缩摘要；非空时会拼到 system 后段
    compact_summary: str = ""
    # Phase 6 起：本轮活跃 skills 拼成的段落；engine 在每轮 LLM 调用前刷新
    active_skills_text: str = ""
    # Phase 7 起：CLAUDE.md（全局+项目）+ memdir 索引块，拼到 prompt 最顶部
    memory_block_text: str = ""
    # 2026-05-26 起：本轮命中的 persona module 正文（含 header）；engine 在每轮前刷新
    active_module_text: str = ""
    # 本轮注入的 persona modules listing 段（不含正文，是常驻目录提示给 LLM）
    persona_modules_listing: str = ""
    # 本轮已注入正文的 module id；LoadPersonaModule 工具用来做去重
    active_module_ids: set[str] = field(default_factory=set)
    # 上一轮最后注入的 module id（仅信息用途，给 REPL /stats 看）
    last_active_module_id: str = ""

    def __post_init__(self) -> None:
        # 占位 system 行；真实内容由 engine 在每轮前调 refresh_system_prompt 注入
        if not self.messages:
            self.messages.append(ChatMessage(role="system", content=""))

    @classmethod
    def new(cls, channel: str, user_id: str | None = None) -> Session:
        return cls(
            session_id=str(uuid.uuid4()),
            channel=channel,
            user_id=user_id,
        )

    def add_user(self, content: MessageContent) -> ChatMessage:
        """Phase 10：content 可以是 str（纯文本）或 list[dict]（OpenAI 多模态格式）。"""
        msg = ChatMessage(role="user", content=content)
        self.messages.append(msg)
        return msg

    def add_assistant(self, text: str) -> ChatMessage:
        # assistant 输出目前仍是纯文本（LLM 流式 yield 的 text）；多模态生成留给后续
        msg = ChatMessage(role="assistant", content=text)
        self.messages.append(msg)
        return msg

    def _effective_system(self) -> str:
        """合并顺序（空段跳过）：
        memory_block → core_persona(messages[0]) → persona_modules_listing
        → active_module(正文) → active_skills → compact_summary → reply_length_anchor
        """
        if self.messages and self.messages[0].role == "system":
            raw = self.messages[0].content
            # system 消息 content 协议上是 str；多模态仅出现在 user 角色 —— 这里做一次防御性 str 化
            persona = raw if isinstance(raw, str) else ""
        else:
            persona = ""
        parts = [
            p for p in (
                self.memory_block_text,
                persona,
                self.persona_modules_listing,
                self.active_module_text,
                self.active_skills_text,
                self.compact_summary,
                _REPLY_LENGTH_ANCHOR,
            ) if p
        ]
        return _SUMMARY_JOINER.join(parts)

    def to_openai_messages(self) -> list[dict[str, Any]]:
        """OpenAI 入参；空 system 被过滤，compact_summary 拼到 system 末尾。"""
        out: list[dict[str, Any]] = []
        sys_text = self._effective_system()
        if sys_text:
            out.append({"role": "system", "content": sys_text})
        for m in self.messages[1:] if self.messages and self.messages[0].role == "system" else self.messages:
            out.append(m.to_openai())
        return out

    def refresh_system_prompt(self, persona: PersonaSnapshot) -> None:
        """用最新人设快照替换 system；不动 compact_summary。"""
        text = build_system_prompt(persona)
        if self.messages and self.messages[0].role == "system":
            self.messages[0] = ChatMessage(role="system", content=text)
        else:
            self.messages.insert(0, ChatMessage(role="system", content=text))
