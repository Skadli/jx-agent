"""对话引擎；Phase 5 起接入 tool_call 循环，无工具时仍走原流式路径。"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from typing import Any

from sanshiliu.context.manager import ContextManager
from sanshiliu.engine.session import Session
from sanshiliu.engine.types import ChatMessage, MessageContent
from sanshiliu.foundation.logging import get_logger
from sanshiliu.foundation.msg_split import DEFAULT_SENTINEL
from sanshiliu.identity.loader import PersonaLoader
from sanshiliu.identity.module_activator import PersonaModuleActivator
from sanshiliu.llm.client import LLMClient
from sanshiliu.llm.router import LLMRouter
from sanshiliu.llm.stream import StreamDelta
from sanshiliu.memory.longterm.claudemd import ClaudeMdLoader
from sanshiliu.memory.longterm.consolidate import MemoryConsolidator
from sanshiliu.memory.longterm.extract import MemoryExtractor
from sanshiliu.memory.longterm.memdir import MemdirLoader
from sanshiliu.memory.shortterm import ShortTermMemory
from sanshiliu.skills.activator import SkillActivator
from sanshiliu.storage.db import Database
from sanshiliu.tools.dispatcher import ToolDispatcher, parse_tool_calls
from sanshiliu.tools.registry import ToolRegistry
from sanshiliu.tools.types import ToolLoopState

_logger = get_logger(__name__)

# 同一 (name, args) 触发 dedupe 的次数；> 此值的同一调用拒绝执行
# fail-fast：相同调用第 3 次直接拒（前 2 次允许，第 3 次返"重复"错误）
_DEDUPE_THRESHOLD = 2


def _flatten_user_text(content: MessageContent) -> str:
    """把多模态 content 摊成纯文本，给 skills 匹配 / memory_extractor 这类只关心文字的下游用。

    str → 原样返回；list → 提取所有 type==text 的 text 字段拼接。
    """
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "text":
            t = part.get("text")
            if isinstance(t, str):
                parts.append(t)
    return " ".join(parts)


class ConversationEngine:
    """持有 LLM / DB / PersonaLoader / ContextManager / ToolRegistry+Dispatcher。"""

    def __init__(
        self,
        llm: LLMClient | LLMRouter,
        db: Database | None = None,
        persona_loader: PersonaLoader | None = None,
        context_manager: ContextManager | None = None,
        tool_registry: ToolRegistry | None = None,
        tool_dispatcher: ToolDispatcher | None = None,
        skill_activator: SkillActivator | None = None,
        claudemd_loader: ClaudeMdLoader | None = None,
        memdir_loader: MemdirLoader | None = None,
        memory_extractor: MemoryExtractor | None = None,
        persona_module_activator: PersonaModuleActivator | None = None,
        short_term: ShortTermMemory | None = None,
        consolidate_instruction: str | None = None,
    ) -> None:
        # Phase 10：llm 可以是单 LLMClient（Phase 1-9 行为）或 LLMRouter（多后端）
        # 两者接口一致：chat / stream_chat / close / model / base_url
        self._llm = llm
        self._db = db
        self._persona_loader = persona_loader
        self._context_manager = context_manager
        self._tool_registry = tool_registry
        self._tool_dispatcher = tool_dispatcher
        self._skill_activator = skill_activator
        self._claudemd_loader = claudemd_loader
        self._memdir_loader = memdir_loader
        self._memory_extractor = memory_extractor
        self._persona_module_activator = persona_module_activator
        # PR1：每条新 message 后调 short_term.append_message 落 jsonl
        self._short_term = short_term
        # PR4：/memory consolidate 用；lazy 构造，命令首次触发时缓存
        self._consolidate_instruction = consolidate_instruction
        self._consolidator: MemoryConsolidator | None = None

    @property
    def llm(self) -> LLMClient | LLMRouter:
        return self._llm

    def get_memory_consolidator(self) -> MemoryConsolidator | None:
        """lazy 构造 MemoryConsolidator；缺少 memdir_loader 或 instruction 时返 None。"""
        if self._consolidator is not None:
            return self._consolidator
        if self._memdir_loader is None or self._consolidate_instruction is None:
            return None
        self._consolidator = MemoryConsolidator(
            llm=self._llm,
            memdir_loader=self._memdir_loader,
            instruction=self._consolidate_instruction,
        )
        return self._consolidator

    @property
    def persona_loader(self) -> PersonaLoader | None:
        return self._persona_loader

    @property
    def context_manager(self) -> ContextManager | None:
        return self._context_manager

    @property
    def tools_enabled(self) -> bool:
        return (
            self._tool_registry is not None
            and not self._tool_registry.is_empty
            and self._tool_dispatcher is not None
        )

    def _refresh_persona(self, session: Session) -> None:
        if self._persona_loader is None:
            return
        try:
            snap = self._persona_loader.get()
        except Exception as exc:
            _logger.error("拉取人设快照失败（保留旧 system）", error=str(exc))
            return
        session.refresh_system_prompt(snap)

    async def _refresh_memory(self, session: Session) -> None:
        """拼装全局 CLAUDE.md + 项目 CLAUDE.md + memdir 索引 + Recent Sessions 到 memory_block。

        2026-05-27：新增 Recent Sessions 段——同 channel+user_id 最近 5 个 session
        曝光给 LLM，让它能调 LoadMemory({"name":"<uuid>" | "recent"}) 读历史对话。
        """
        parts: list[str] = []
        if self._claudemd_loader is not None:
            try:
                snap = self._claudemd_loader.get()
                txt = snap.assembled()
                if txt:
                    parts.append(txt)
            except Exception as exc:
                _logger.warning("CLAUDE.md 读失败（不阻塞）", error=str(exc))
        if self._memdir_loader is not None:
            try:
                mem_snap = self._memdir_loader.get()
                if mem_snap.index_text.strip():
                    parts.append(
                        "# Long-term Memory Index (memdir)\n\n" + mem_snap.index_text.strip()
                    )
            except Exception as exc:
                _logger.warning("memdir 读失败（不阻塞）", error=str(exc))
        # Recent Sessions：仅在 db 装配时尝试；任何异常都不阻塞主对话
        if self._db is not None:
            try:
                recent = await self._db.list_recent_sessions_for_prompt(
                    channel=session.channel,
                    user_id=session.user_id,
                    limit=5,
                    exclude_id=session.session_id,
                )
                if recent:
                    lines = ["## Recent Sessions (last 5)"]
                    for r in recent:
                        last_ts = r.get("last_active_at")
                        ts_str = ""
                        if isinstance(last_ts, int):
                            ts_str = datetime.fromtimestamp(
                                last_ts / 1000,
                            ).strftime("%Y-%m-%d %H:%M")
                        lines.append(
                            f"- {r['id']} · channel={r['channel']} · {ts_str}"
                        )
                    lines.append("")
                    lines.append(
                        '可调 `LoadMemory({"name":"<uuid>"})` 或 '
                        '`LoadMemory({"name":"recent"})` 读取。'
                    )
                    parts.append("\n".join(lines))
            except Exception as exc:
                _logger.warning("Recent Sessions 注入失败（不阻塞）", error=str(exc))
        session.memory_block_text = "\n\n---\n\n".join(parts) if parts else ""

    def _refresh_persona_modules(self, session: Session, user_text: str) -> None:
        """关键词命中 0-1 个 persona module 注入正文 + 总是写 listing 段（如有 module）。

        清空 active_module_ids 是每轮的起点——本轮 LoadPersonaModule 工具
        通过这个集合判断「引擎是否已经注入过 X」做去重。
        """
        session.active_module_ids = set()
        session.active_module_text = ""
        session.persona_modules_listing = ""
        if self._persona_module_activator is None:
            session.last_active_module_id = ""
            return
        try:
            listing = self._persona_module_activator.list_for_prompt()
            session.persona_modules_listing = listing
            picked = self._persona_module_activator.pick(user_text)
            if picked is not None:
                session.active_module_text = self._persona_module_activator.render_body(picked)
                session.active_module_ids.add(picked.id)
                session.last_active_module_id = picked.id
            else:
                session.last_active_module_id = ""
        except Exception as exc:
            _logger.error("persona module 激活失败（保留旧 listing/module）", error=str(exc))

    def _refresh_skills(self, session: Session, user_text: str) -> None:
        """注入 skills listing 到 system prompt；和 Claude Code 一致——
        listing 只给 name+description，正文由 LLM 主动调 Skill 工具拿。
        user_text 仅作签名兼容，listing 不再做关键字预匹配。"""
        del user_text  # 显式标记不用，避免静态检查警告
        if self._skill_activator is None:
            session.active_skills_text = ""
            return
        try:
            session.active_skills_text = self._skill_activator.list_for_prompt()
        except Exception as exc:
            _logger.error("skill listing 构造失败（保留旧 listing）", error=str(exc))

    async def _maybe_compact(self, session: Session) -> None:
        if self._context_manager is None:
            return
        try:
            compacted = await self._context_manager.ensure_within_budget(session)
        except Exception as exc:
            _logger.error("compact 阶段异常（不阻塞主对话）", error=str(exc))
            return
        # PR1：真发生 compact 时把 summary 落到 sqlite，确保进程重启后能 reload
        if compacted:
            await self.persist_session_state(session)

    async def persist_session_state(self, session: Session) -> None:
        """PR1：把 compact_summary + active_module_ids 落到 sqlite sessions 表。

        messages 走 shortterm jsonl；这里只存"非消息"的会话状态。失败不阻塞。
        commands.py 的 /compact 命令也会调用此方法。
        """
        if self._db is None:
            return
        try:
            await self._db.save_session_state(
                session_id=session.session_id,
                compact_summary=session.compact_summary,
                active_module_ids=",".join(sorted(session.active_module_ids)),
            )
        except Exception as exc:
            _logger.warning("save_session_state 失败（不阻塞）", error=str(exc))

    async def _persist_message(self, session: Session, msg: ChatMessage) -> None:
        """PR1：把新增 message 异步落到 shortterm jsonl（per-message append）。

        short_term 内部已 try/except，这里 await 不会因 IO 失败影响主对话。
        compact 时被裁掉的 messages 不再 append（compact_summary 走 sqlite）。
        """
        if self._short_term is None:
            return
        await self._short_term.append_message(session.session_id, msg)

    async def stream_turn(
        self, session: Session, user_text: MessageContent,
    ) -> AsyncIterator[StreamDelta]:
        """流式接口；有工具时退化为整段一次性 yield（Phase 5 限制）。

        Phase 10：user_text 可为 str（旧）或 list[dict]（OpenAI 多模态格式）。
        """
        if self.tools_enabled:
            # 工具前的口语 preamble 作为独立气泡/消息浮出（"状态→结果"两段流）。
            # 段间插 DEFAULT_SENTINEL，复用各通道已有的 <MSG> 拆分逻辑切气泡。
            preambles: list[str] = []

            async def _collect_preamble(text: str) -> None:
                preambles.append(text)

            msg = await self.complete_turn(
                session, user_text, on_user_message=_collect_preamble,
            )
            for pre in preambles:
                yield StreamDelta(text=pre)
                yield StreamDelta(text=DEFAULT_SENTINEL)
            yield StreamDelta(text=msg.text_only())
            return

        flat_text = _flatten_user_text(user_text)
        await self._refresh_memory(session)
        self._refresh_persona(session)
        self._refresh_persona_modules(session, flat_text)
        # _refresh_skills 不消费 user_text 内容（已 `del`），任何 union 类型都行
        self._refresh_skills(session, flat_text)
        await self._maybe_compact(session)
        user_msg = session.add_user(user_text)
        await self._persist_message(session, user_msg)
        await self._touch_session(session)

        chunks: list[str] = []
        try:
            async for delta in self._llm.stream_chat(
                messages=session.to_openai_messages(),
                session_id=session.session_id,
                channel=session.channel,
                user_id=session.user_id,
            ):
                chunks.append(delta.text)
                yield delta
        finally:
            assistant_text = "".join(chunks)
            assistant_msg = session.add_assistant(assistant_text)
            await self._persist_message(session, assistant_msg)
            # 流式路径下 budget 反查必须在 finally，否则客户端早断不会执行
            await self._refresh_budget_from_db(session)
            # 异步触发 auto-extract（V-7：失败不阻塞）；只用文本部分
            if assistant_text and self._memory_extractor is not None:
                self._memory_extractor.schedule(
                    user_text=_flatten_user_text(user_text),
                    assistant_text=assistant_text,
                    session_id=session.session_id,
                )

    async def complete_turn(
        self, session: Session, user_text: MessageContent,
        *, on_user_message: Callable[[str], Awaitable[None]] | None = None,
    ) -> ChatMessage:
        """非流式 + tool_call 循环；返回最终 assistant 消息。

        Phase 10：user_text 可为 str 或 list[dict]（OpenAI 多模态格式）。

        on_user_message：可选回调。当某轮模型在调用工具的同时写了口语 preamble
        （content 非空 + tool_calls），在执行工具**前**用该 preamble 调用一次，
        让通道把它作为独立的"状态"消息先发出（拟人化"状态→结果"）。最终回复仍走返回值。
        dream_runner 等不传则保持原单条行为。
        """
        flat_text = _flatten_user_text(user_text)
        await self._refresh_memory(session)
        self._refresh_persona(session)
        self._refresh_persona_modules(session, flat_text)
        self._refresh_skills(session, flat_text)
        await self._maybe_compact(session)
        user_msg = session.add_user(user_text)
        await self._persist_message(session, user_msg)
        await self._touch_session(session)

        loop_state = ToolLoopState(max_turns=6)
        while True:
            loop_state.turn += 1
            if loop_state.turn > loop_state.max_turns:
                _logger.warning("tool_call 循环超限", session_id=session.session_id, turn=loop_state.turn)
                limit_msg = session.add_assistant("[已达 tool 调用上限，请缩小问题范围或重试]")
                await self._persist_message(session, limit_msg)
                return limit_msg

            # tools_enabled 已确保 self._tool_registry is not None；mypy 看不出
            if self.tools_enabled:
                assert self._tool_registry is not None
                tools: list[dict[str, Any]] | None = self._tool_registry.to_openai_tools()
            else:
                tools = None
            result = await self._llm.chat(
                messages=session.to_openai_messages(),
                session_id=session.session_id,
                channel=session.channel,
                user_id=session.user_id,
                tools=tools,
            )
            if self._context_manager is not None:
                self._context_manager.record_usage(
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                )

            if not result.tool_calls:
                msg = session.add_assistant(result.text)
                await self._persist_message(session, msg)
                # 异步触发 auto-extract（V-7：失败不阻塞）；只用文本部分
                if result.text and self._memory_extractor is not None:
                    self._memory_extractor.schedule(
                        user_text=_flatten_user_text(user_text),
                        assistant_text=result.text,
                        session_id=session.session_id,
                    )
                return msg

            # 有 tool_calls：追加 assistant 占位 + 执行每个 call → tool 消息
            # DeepSeek reasoner 系列要求 reasoning_content 原样回传，否则下轮 400
            asst_with_tools = ChatMessage(
                role="assistant",
                content=result.text or "",
                tool_calls=result.tool_calls,
                reasoning_content=result.reasoning_content or None,
            )
            session.messages.append(asst_with_tools)
            await self._persist_message(session, asst_with_tools)
            # 工具前的口语 preamble 浮出为独立"状态"消息（拟人化"状态→结果"）
            if on_user_message is not None and result.text and result.text.strip():
                await on_user_message(result.text)
            parsed_calls = parse_tool_calls(result.tool_calls)
            for tc in parsed_calls:
                tool_started = time.monotonic()
                count = loop_state.remember(tc.name, tc.arguments)
                # PR3：dedupe 路径没走 dispatcher，permission_decision 保持 None（合理）
                permission_decision: str | None = None
                if count > _DEDUPE_THRESHOLD:
                    tool_result_text = (
                        f"[同一调用 {tc.name}(...) 已重复 {count} 次，被去重；请换不同参数或退出工具循环]"
                    )
                    is_error = True
                else:
                    assert self._tool_dispatcher is not None
                    res = await self._tool_dispatcher.execute(tc, session_id=session.session_id)
                    tool_result_text = res.content
                    is_error = res.is_error
                    permission_decision = res.permission_decision
                await self._record_tool_call(
                    session_id=session.session_id,
                    tool_name=tc.name,
                    arguments=tc.arguments,
                    result_text=tool_result_text,
                    is_error=is_error,
                    latency_ms=int((time.monotonic() - tool_started) * 1000),
                    permission_decision=permission_decision,
                )
                _logger.info(
                    "tool 调用完成",
                    tool=tc.name, turn=loop_state.turn, is_error=is_error,
                )
                tool_msg = ChatMessage(
                    role="tool",
                    content=tool_result_text,
                    tool_call_id=tc.id,
                    name=tc.name,
                )
                session.messages.append(tool_msg)
                await self._persist_message(session, tool_msg)

            # 下一轮 LLM 调用前再压一次：折叠超长 tool_result + 超阈值时整段 compact。
            # 与 Claude Code 在 tool 循环内做 microcompact 的策略一致；
            # 上一轮的 record_usage 已刷新 last_prompt_tokens，阈值判定能正确触发。
            await self._maybe_compact(session)

    async def _refresh_budget_from_db(self, session: Session) -> None:
        """流式路径下 budget 靠 DB 反查 llm_calls 最近一行；过滤 compact-internal 通道。"""
        if self._context_manager is None or self._db is None:
            return
        try:
            cur = await self._db._execute(
                """
                SELECT input_tokens, output_tokens FROM llm_calls
                WHERE session_id = ? AND channel != 'compact-internal'
                ORDER BY id DESC LIMIT 1
                """,
                (session.session_id,),
            )
            row = cur.fetchone()
        except Exception as exc:
            _logger.error("budget 反查 llm_calls 失败（不阻塞）", error=str(exc))
            return
        if row is None:
            return
        self._context_manager.record_usage(
            input_tokens=int(row["input_tokens"] or 0),
            output_tokens=int(row["output_tokens"] or 0),
        )

    async def _touch_session(self, session: Session) -> None:
        if self._db is None:
            return
        try:
            await self._db.upsert_session(
                session_id=session.session_id,
                channel=session.channel,
                user_id=session.user_id,
            )
        except Exception as exc:
            _logger.error("upsert_session 失败（不阻塞）", error=str(exc))

    async def _record_tool_call(
        self,
        *,
        session_id: str,
        tool_name: str,
        arguments: dict[str, object],
        result_text: str,
        is_error: bool,
        latency_ms: int,
        permission_decision: str | None = None,
    ) -> None:
        if self._db is None:
            return
        try:
            await self._db.insert_tool_call(
                session_id=session_id,
                tool_name=tool_name,
                arguments=json.dumps(arguments, ensure_ascii=False, sort_keys=True),
                result_text=result_text,
                is_error=is_error,
                latency_ms=latency_ms,
                permission_decision=permission_decision,
            )
        except Exception as exc:
            _logger.error("tool_calls 落库失败（不阻塞）", tool=tool_name, error=str(exc))
