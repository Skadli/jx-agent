"""对话引擎；Phase 5 起接入 tool_call 循环，无工具时仍走原流式路径。"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator

from sanshiliu.context.manager import ContextManager
from sanshiliu.engine.session import Session
from sanshiliu.engine.types import ChatMessage
from sanshiliu.foundation.logging import get_logger
from sanshiliu.identity.loader import PersonaLoader
from sanshiliu.llm.client import LLMClient
from sanshiliu.llm.stream import StreamDelta
from sanshiliu.memory.longterm.claudemd import ClaudeMdLoader
from sanshiliu.memory.longterm.extract import MemoryExtractor
from sanshiliu.memory.longterm.memdir import MemdirLoader
from sanshiliu.skills.activator import SkillActivator
from sanshiliu.storage.db import Database
from sanshiliu.tools.dispatcher import ToolDispatcher, parse_tool_calls
from sanshiliu.tools.registry import ToolRegistry
from sanshiliu.tools.types import ToolLoopState

_logger = get_logger(__name__)

# 同一 (name, args) 触发 dedupe 的次数；> 此值的同一调用拒绝执行
_DEDUPE_THRESHOLD = 4


class ConversationEngine:
    """持有 LLM / DB / PersonaLoader / ContextManager / ToolRegistry+Dispatcher。"""

    def __init__(
        self,
        llm: LLMClient,
        db: Database | None = None,
        persona_loader: PersonaLoader | None = None,
        context_manager: ContextManager | None = None,
        tool_registry: ToolRegistry | None = None,
        tool_dispatcher: ToolDispatcher | None = None,
        skill_activator: SkillActivator | None = None,
        claudemd_loader: ClaudeMdLoader | None = None,
        memdir_loader: MemdirLoader | None = None,
        memory_extractor: MemoryExtractor | None = None,
    ) -> None:
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

    @property
    def llm(self) -> LLMClient:
        return self._llm

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

    def _refresh_memory(self, session: Session) -> None:
        """拼装全局 CLAUDE.md + 项目 CLAUDE.md + memdir MEMORY.md 索引到 memory_block。"""
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
        session.memory_block_text = "\n\n---\n\n".join(parts) if parts else ""

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
            await self._context_manager.ensure_within_budget(session)
        except Exception as exc:
            _logger.error("compact 阶段异常（不阻塞主对话）", error=str(exc))

    async def stream_turn(self, session: Session, user_text: str) -> AsyncIterator[StreamDelta]:
        """流式接口；有工具时退化为整段一次性 yield（Phase 5 限制）。"""
        if self.tools_enabled:
            msg = await self.complete_turn(session, user_text)
            yield StreamDelta(text=msg.content)
            return

        self._refresh_memory(session)
        self._refresh_persona(session)
        self._refresh_skills(session, user_text)
        await self._maybe_compact(session)
        session.add_user(user_text)
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
            session.add_assistant(assistant_text)
            # 流式路径下 budget 反查必须在 finally，否则客户端早断不会执行
            await self._refresh_budget_from_db(session)
            # 异步触发 auto-extract（V-7：失败不阻塞）
            if assistant_text and self._memory_extractor is not None:
                self._memory_extractor.schedule(
                    user_text=user_text, assistant_text=assistant_text,
                    session_id=session.session_id,
                )

    async def complete_turn(self, session: Session, user_text: str) -> ChatMessage:
        """非流式 + tool_call 循环；返回最终 assistant 消息。"""
        self._refresh_memory(session)
        self._refresh_persona(session)
        self._refresh_skills(session, user_text)
        await self._maybe_compact(session)
        session.add_user(user_text)
        await self._touch_session(session)

        loop_state = ToolLoopState(max_turns=10)
        while True:
            loop_state.turn += 1
            if loop_state.turn > loop_state.max_turns:
                _logger.warning("tool_call 循环超限", session_id=session.session_id, turn=loop_state.turn)
                return session.add_assistant("[已达 tool 调用上限，请缩小问题范围或重试]")

            tools = self._tool_registry.to_openai_tools() if self.tools_enabled else None
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
                # 异步触发 auto-extract（V-7：失败不阻塞）
                if result.text and self._memory_extractor is not None:
                    self._memory_extractor.schedule(
                        user_text=user_text, assistant_text=result.text,
                        session_id=session.session_id,
                    )
                return msg

            # 有 tool_calls：追加 assistant 占位 + 执行每个 call → tool 消息
            # DeepSeek reasoner 系列要求 reasoning_content 原样回传，否则下轮 400
            session.messages.append(ChatMessage(
                role="assistant",
                content=result.text or "",
                tool_calls=result.tool_calls,
                reasoning_content=result.reasoning_content or None,
            ))
            parsed_calls = parse_tool_calls(result.tool_calls)
            for tc in parsed_calls:
                tool_started = time.monotonic()
                count = loop_state.remember(tc.name, tc.arguments)
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
                await self._record_tool_call(
                    session_id=session.session_id,
                    tool_name=tc.name,
                    arguments=tc.arguments,
                    result_text=tool_result_text,
                    is_error=is_error,
                    latency_ms=int((time.monotonic() - tool_started) * 1000),
                )
                _logger.info(
                    "tool 调用完成",
                    tool=tc.name, turn=loop_state.turn, is_error=is_error,
                )
                session.messages.append(ChatMessage(
                    role="tool",
                    content=tool_result_text,
                    tool_call_id=tc.id,
                    name=tc.name,
                ))

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
                permission_decision=None,
            )
        except Exception as exc:
            _logger.error("tool_calls 落库失败（不阻塞）", tool=tool_name, error=str(exc))
