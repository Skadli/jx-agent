"""对话循环（Phase 1 最小版）。

一轮 turn：用户消息 → LLM 流式响应 → 追加到 session 历史。
没有 tools / memory / skills；后续 phase 在此处分别接入。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sanshiliu.engine.session import Session
from sanshiliu.engine.types import ChatMessage
from sanshiliu.foundation.logging import get_logger
from sanshiliu.llm.client import LLMClient
from sanshiliu.llm.stream import StreamDelta
from sanshiliu.storage.db import Database

_logger = get_logger(__name__)


class ConversationEngine:
    """对话引擎（Phase 1 极简版）。

    持有 LLM 客户端和 DB；channel 持有 Session 并调 :meth:`stream_turn`。
    """

    def __init__(self, llm: LLMClient, db: Database | None = None) -> None:
        self._llm = llm
        self._db = db

    @property
    def llm(self) -> LLMClient:
        return self._llm

    async def stream_turn(self, session: Session, user_text: str) -> AsyncIterator[StreamDelta]:
        """跑一轮对话，实时 yield assistant 增量。

        - 把 user 消息追加到 session
        - 跑 LLM 流式
        - 累积 assistant 文本，结束后追加到 session
        - llm_calls 表由 LLMClient 自动落
        """
        session.add_user(user_text)
        await self._touch_session(session)

        chunks: list[str] = []
        async for delta in self._llm.stream_chat(
            messages=session.to_openai_messages(),
            session_id=session.session_id,
            channel=session.channel,
            user_id=session.user_id,
        ):
            chunks.append(delta.text)
            yield delta

        assistant_text = "".join(chunks)
        if assistant_text:
            session.add_assistant(assistant_text)
        else:
            # 极少数空回复：留个空 assistant 占位，避免后续 LLM 看到 user/user 连续
            session.add_assistant("")
            _logger.warning("LLM 回复为空", session_id=session.session_id)

    async def complete_turn(self, session: Session, user_text: str) -> ChatMessage:
        """一次性等结果的版本（非流式 UI 用）。返回 assistant 消息。"""
        session.add_user(user_text)
        await self._touch_session(session)

        result = await self._llm.chat(
            messages=session.to_openai_messages(),
            session_id=session.session_id,
            channel=session.channel,
            user_id=session.user_id,
        )
        return session.add_assistant(result.text)

    async def _touch_session(self, session: Session) -> None:
        """刷新 sessions 表 last_active_at；db=None 时跳过。"""
        if self._db is None:
            return
        try:
            await self._db.upsert_session(
                session_id=session.session_id,
                channel=session.channel,
                user_id=session.user_id,
            )
        except Exception as exc:
            # 不阻塞对话：DB 抖动不应让用户感知
            _logger.error("upsert_session 失败（不阻塞）", error=str(exc))
