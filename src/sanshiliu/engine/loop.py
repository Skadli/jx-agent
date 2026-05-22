"""对话引擎；Phase 5 起接入 tool_call 循环，无工具时仍走原流式路径。"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sanshiliu.context.manager import ContextManager
from sanshiliu.engine.session import Session
from sanshiliu.engine.types import ChatMessage
from sanshiliu.foundation.logging import get_logger
from sanshiliu.identity.loader import PersonaLoader
from sanshiliu.llm.client import LLMClient
from sanshiliu.llm.stream import StreamDelta
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
    ) -> None:
        self._llm = llm
        self._db = db
        self._persona_loader = persona_loader
        self._context_manager = context_manager
        self._tool_registry = tool_registry
        self._tool_dispatcher = tool_dispatcher
        self._skill_activator = skill_activator

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

    def _refresh_skills(self, session: Session, user_text: str) -> None:
        """根据本轮用户输入激活 skills；空 activator 时清空避免上轮残留。"""
        if self._skill_activator is None:
            session.active_skills_text = ""
            return
        try:
            actives = self._skill_activator.activate_for(user_text)
            session.active_skills_text = self._skill_activator.to_prompt_addition(actives)
        except Exception as exc:
            _logger.error("skill 激活失败（保留旧 actives）", error=str(exc))

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

    async def complete_turn(self, session: Session, user_text: str) -> ChatMessage:
        """非流式 + tool_call 循环；返回最终 assistant 消息。"""
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
                return session.add_assistant(result.text)

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
                count = loop_state.remember(tc.name, tc.arguments)
                if count > _DEDUPE_THRESHOLD:
                    tool_result_text = (
                        f"[同一调用 {tc.name}(...) 已重复 {count} 次，被去重；请换不同参数或退出工具循环]"
                    )
                    is_error = True
                else:
                    assert self._tool_dispatcher is not None
                    res = await self._tool_dispatcher.execute(tc)
                    tool_result_text = res.content
                    is_error = res.is_error
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

    async def _refresh_budget_from_db(self, session: Session) -> None:
        """流式路径下 budget 靠 DB 反查 llm_calls 最近一行；过滤 compact-internal 通道。"""
        if self._context_manager is None or self._db is None:
            return
        try:
            cur = await self._db._execute(  # noqa: SLF001
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
