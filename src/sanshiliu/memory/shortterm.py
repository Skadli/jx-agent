"""短期记忆；per-message append jsonl + reload。

PR1（2026-05-27）：从 session-level snapshot 改为 Claude Code 风格 per-message append。
- append_message()：每条新 message 触发 fire-and-forget 落 jsonl
- reload()：读 jsonl 反序列化回 list[ChatMessage]
- snapshot()：保留旧接口语义但内部实现可降级；目前仍是"全量重写最后一帧"
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sanshiliu.engine.types import ChatMessage
from sanshiliu.foundation.logging import get_logger
from sanshiliu.storage.jsonl import JsonlWriter

if TYPE_CHECKING:
    from sanshiliu.engine.session import Session

_logger = get_logger(__name__)


class ShortTermMemory:
    """短期记忆 = jsonl per-message append + reload。

    每条 message 是 jsonl 一行，含 ts/role/content/tool_calls 等字段。
    与 Claude Code transcript（~/.claude/projects/<id>/<session>.jsonl）格式兼容。
    """

    def __init__(self, base_dir: Path) -> None:
        self._writer = JsonlWriter(base_dir)

    @property
    def writer(self) -> JsonlWriter:
        return self._writer

    def jsonl_path(self, session_id: str) -> Path:
        """调用方查询某 session 的 jsonl 文件位置。"""
        return self._writer.path_for(session_id)

    async def append_message(self, session_id: str, msg: ChatMessage) -> None:
        """追加一条 message 到 session jsonl；失败仅记日志，不阻塞主对话。"""
        record: dict[str, Any] = {
            "ts": int(time.time() * 1000),
            "role": msg.role,
            "content": msg.content,
        }
        if msg.tool_calls:
            record["tool_calls"] = msg.tool_calls
        if msg.tool_call_id:
            record["tool_call_id"] = msg.tool_call_id
        if msg.name:
            record["name"] = msg.name
        if msg.reasoning_content:
            record["reasoning_content"] = msg.reasoning_content
        try:
            await self._writer.append(session_id, record)
        except Exception as exc:
            _logger.warning("append_message 失败（不阻塞）", session_id=session_id, error=str(exc))

    async def reload(self, session_id: str) -> list[ChatMessage]:
        """读回 session 的 messages；jsonl 不存在或全坏返回空列表。

        约定：jsonl 仅含 message 行；compact_summary 存 sqlite sessions 表。
        调用方负责把第 0 个 system 占位补上（如有）。
        """
        try:
            rows = await self._writer.read_all(session_id)
        except Exception as exc:
            _logger.warning("reload 失败（返回空 messages）", session_id=session_id, error=str(exc))
            return []
        out: list[ChatMessage] = []
        for r in rows:
            role = r.get("role")
            if role not in ("system", "user", "assistant", "tool"):
                continue
            content = r.get("content")
            # 兼容旧版（session-level snapshot）：'messages' 字段套娃
            if content is None and "messages" in r:
                # 旧记录是整 session dump，跳过——按 PR1 后语义只接受 per-message
                continue
            out.append(ChatMessage(
                role=role,
                content=content if content is not None else "",
                tool_calls=r.get("tool_calls"),
                tool_call_id=r.get("tool_call_id"),
                name=r.get("name"),
                reasoning_content=r.get("reasoning_content"),
            ))
        return out

    async def snapshot(self, session: Session) -> None:
        """旧接口：把当前 session 整体快照写一行（PR1 前的语义）。

        仍保留是因为 cmd_new 调用它做"封档"。新对话循环走 append_message。
        """
        record = {
            "ts": int(time.time() * 1000),
            "type": "snapshot",
            "session_id": session.session_id,
            "channel": session.channel,
            "user_id": session.user_id,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "tool_calls": m.tool_calls,
                    "tool_call_id": m.tool_call_id,
                    "name": m.name,
                }
                for m in session.messages
            ],
            "compact_summary": session.compact_summary,
        }
        try:
            await self._writer.append(session.session_id, record)
        except Exception as exc:
            _logger.warning("短期记忆快照失败（不阻塞）", error=str(exc))

    @staticmethod
    def to_jsonl_line(session: Session) -> str:
        """同步版；测试用。"""
        record = {
            "session_id": session.session_id,
            "messages": [{"role": m.role, "content": m.content} for m in session.messages],
        }
        return json.dumps(record, ensure_ascii=False)
