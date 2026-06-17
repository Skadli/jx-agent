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

# C4：compact 后给模型的"续聊"指令，拼在 compact_summary 之后（仅出参拼装、不写回 compact_summary
# 字段，避免污染下一次 compact 的输入）。对齐 CC getCompactUserSummaryMessage："据摘要继续、别复述"。
_COMPACT_CONTINUE_NOTE = (
    "（以上是早前对话的摘要。直接据此继续，就当对话没断过；别向用户复述或确认这段摘要。）"
)

# T1：半截工具轮里，给"未回应的 tool_call"补的合成占位结果（对齐 CC 的
# SYNTHETIC_TOOL_RESULT_PLACEHOLDER）——保留 assistant.tool_calls，只补一条 is_error 占位，
# 既消除"孤儿 tool_call 触发 400"，又不丢 assistant 已有信息。
_SYNTHETIC_TOOL_RESULT = "<tool_use_error>工具调用未完成（上一轮被中断），本次无结果。</tool_use_error>"


def _tool_call_ids(msg: ChatMessage) -> set[str]:
    """assistant 消息里所有带 id 的 tool_call id；无 tool_calls / 无 id 返空集。"""
    if not msg.tool_calls:
        return set()
    return {
        cid
        for c in msg.tool_calls
        if isinstance(c, dict) and isinstance(cid := c.get("id"), str)
    }


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
    # S3/C3：本会话已调用过的 skill → 正文（按调用顺序，末尾=最近）。用于 compact 折掉 skill body 后
    # 在出参期重注入 <invoked-skills> 附件，让 skill 指令跨 compact 存活。不进 jsonl（运行期状态）。
    invoked_skills: dict[str, str] = field(default_factory=dict)

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

    def remember_invoked_skill(self, name: str, body: str) -> None:
        """记录一次成功的 Skill 调用（name→正文）；重复调用则移到末尾标记为最近。"""
        if not name or not body:
            return
        self.invoked_skills.pop(name, None)
        self.invoked_skills[name] = body

    def _invoked_skills_attachment(self, src: list[ChatMessage]) -> str:
        """构造 <invoked-skills> 附件：把记录过、但当前 messages 里已不在场（被 compact 折掉）的
        skill 正文按最近优先重注入；单条 ≤5K、总量 ≤25K、最多 5 个（对齐 CC 的 5K/25K 量级）。
        仍存活于某条 tool 消息的 skill 不重注入（避免重复）。无可注入则返回空串。
        """
        if not self.invoked_skills:
            return ""
        live = {m.content for m in src if m.role == "tool" and isinstance(m.content, str)}
        blocks: list[str] = []
        total = 0
        for name, body in reversed(self.invoked_skills.items()):  # 末尾=最近 → 最近优先
            if body in live:
                continue
            piece = body[:5000]
            if len(blocks) >= 5 or total + len(piece) > 25000:
                break
            blocks.append(f"## {name}\n{piece}")
            total += len(piece)
        if not blocks:
            return ""
        return (
            "<invoked-skills>\n以下是本会话已调用过的 skill 正文（压缩后重注入，供你继续遵循其指令）：\n\n"
            + "\n\n".join(blocks)
            + "\n</invoked-skills>"
        )

    def _effective_system(self) -> str:
        """合并顺序（空段跳过）：静态段在前、易变段在后，让 DeepSeek 自动前缀缓存吃到稳定前缀。
        core_persona(messages[0]) → persona_modules_listing → active_module(正文)
        → memory_block → compact_summary → reply_length_anchor
        （active_skills 已移出：作为 <system-reminder> 贴用户消息注入，见 to_openai_messages）

        为什么 memory_block 从最前挪到静态段之后：它含 Recent Sessions，每个 session 都变；
        原来排第一会让它后面所有静态人格的前缀缓存整段失效，每轮都得重算。静态大块前置后，
        跨轮/跨 session 的相同前缀才能命中缓存。anchor 仍留最末尾吃 recency。
        """
        if self.messages and self.messages[0].role == "system":
            raw = self.messages[0].content
            # system 消息 content 协议上是 str；多模态仅出现在 user 角色 —— 这里做一次防御性 str 化
            persona = raw if isinstance(raw, str) else ""
        else:
            persona = ""
        # 注意：active_skills_text 不在这里——它作为 <system-reminder> 贴本轮用户消息注入
        # （见 to_openai_messages），吃 recency 而非埋在静态 system 中段。
        # C4：compact_summary 非空时拼一句"据摘要继续、别复述"的续聊指令（仅出参拼装，不写回字段）。
        compact_block = (
            f"{self.compact_summary}\n\n{_COMPACT_CONTINUE_NOTE}" if self.compact_summary else ""
        )
        parts = [
            p for p in (
                persona,                       # 静态大块 → 稳定前缀，跨轮/跨 session 命中 DeepSeek 自动前缀缓存
                self.persona_modules_listing,  # 静态
                self.active_module_text,       # 易变（每轮激活）
                self.memory_block_text,        # 易变（Recent Sessions 每 session 变）——必须排在静态段之后
                compact_block,                 # 易变（压缩时变）+ C4 续聊指令
                _REPLY_LENGTH_ANCHOR,          # 留最后吃 recency
            ) if p
        ]
        return _SUMMARY_JOINER.join(parts)

    def to_openai_messages(self) -> list[dict[str, Any]]:
        """OpenAI 入参；空 system 被过滤，compact_summary 拼到 system 末尾。

        安全网：修掉「tool_call 未全部回应」的半截工具轮。web /chat 触达 deadline 或客户端
        断开会 cancel 掉 tool 循环，可能停在「assistant 已挂 tool_calls 但 tool 结果没补齐」
        的状态（in-memory 与 jsonl 都半截）；原样回传会让下一轮 LLM 400（每个 tool_call 必须
        有对应 tool 响应）。这里在出参前为未回应的 tool_call 补一条 is_error 占位、并保留
        assistant.tool_calls（对齐 CC ensureToolResultPairing），既消除 400 又不丢信息。
        """
        src = (
            self.messages[1:]
            if self.messages and self.messages[0].role == "system"
            else self.messages
        )
        answered = {m.tool_call_id for m in src if m.role == "tool" and m.tool_call_id}

        out: list[dict[str, Any]] = []
        sys_text = self._effective_system()
        if sys_text:
            out.append({"role": "system", "content": sys_text})

        # S3/C3：把 compact 折掉的、已调用 skill 的正文作为 <invoked-skills> 附件重注入（贴在 system
        # 之后、历史之前；仍存活于某条 tool 消息的不重注入，避免重复）。
        skill_attach = self._invoked_skills_attachment(src)
        if skill_attach:
            out.append({"role": "user", "content": skill_attach})

        # skills 清单 + 触发规则作为 <system-reminder> 贴在本轮最后一条 user 消息前注入——吃 recency
        # （晚于 system 末尾的长度锚），对齐 CC 的 system-reminder 投递；匹配交给模型读 description
        # （跨语言、对新装 skill 零配置即生效）。不落 session.messages、不进 jsonl，纯出参期注入。
        reminder = self.active_skills_text
        last_user_idx = max(
            (i for i, m in enumerate(src) if m.role == "user"), default=-1
        )
        for i, m in enumerate(src):
            ids = _tool_call_ids(m)
            if ids and not ids <= answered:
                # T1 半截工具轮：保留 assistant 原样（含 tool_calls），为每个未回应的 tool_call 补一条
                # is_error 占位（对齐 CC ensureToolResultPairing 的 SYNTHETIC_TOOL_RESULT_PLACEHOLDER）——
                # 既消除"孤儿 tool_call 触发 400"，又不丢 assistant 已写的正文/工具意图。
                out.append(m.to_openai())
                for missing in sorted(ids - answered):
                    if not missing:
                        continue  # 空 id 无法配对，跳过（T7：空/重复 id 防呆）
                    out.append({
                        "role": "tool",
                        "tool_call_id": missing,
                        "content": _SYNTHETIC_TOOL_RESULT,
                    })
                continue
            if i == last_user_idx and reminder:
                out.append({
                    "role": "user",
                    "content": f"<system-reminder>\n{reminder}\n</system-reminder>",
                })
            out.append(m.to_openai())
        return out

    def refresh_system_prompt(self, persona: PersonaSnapshot) -> None:
        """用最新人设快照替换 system；不动 compact_summary。"""
        text = build_system_prompt(persona)
        if self.messages and self.messages[0].role == "system":
            self.messages[0] = ChatMessage(role="system", content=text)
        else:
            self.messages.insert(0, ChatMessage(role="system", content=text))
