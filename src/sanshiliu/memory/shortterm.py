"""短期记忆；per-message append jsonl + reload。

PR1（2026-05-27）：从 session-level snapshot 改为 Claude Code 风格 per-message append。
- append_message()：每条新 message 触发 fire-and-forget 落 jsonl
- reload()：读 jsonl 反序列化回 list[ChatMessage]
- snapshot()：保留旧接口语义但内部实现可降级；目前仍是"全量重写最后一帧"
"""

from __future__ import annotations

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
        """读回 session 的 messages；兼容三种 jsonl 行格式：

        - per-message：{ts, role, content, tool_calls, ...}（PR1 之后默认写入）
        - 带 type 的 snapshot：{ts, type:"snapshot", messages:[...]}（cmd_new 的封档格式）
        - 不带 type 的旧 snapshot：{ts, session_id, channel, messages:[...], compact_summary}
          （d8a6ffb 之前 web 通道唯一写出的格式——没有 type 标记）

        判定 snapshot 只看「有没有 messages 数组」，不再要求 type=="snapshot"：旧版整帧
        dump 没写 type，之前被漏判成 per-message（既无 role 又无 messages 命中）→ 整段丢弃，
        进程重启后这些会话恢复为空、dashboard 也读不出历史（实测 10 个老会话受影响）。
        三种格式可在同一 jsonl 混合。snapshot 是“截至当时的完整帧”，所以读到 snapshot 时会
        替换此前累计的 per-message，随后再继续追加 snapshot 之后的新行。
        system 消息不从磁盘恢复；调用方会补当前版本的 system 占位并刷新 persona。
        """
        try:
            rows = await self._writer.read_all(session_id)
        except Exception as exc:
            _logger.warning("reload 失败（返回空 messages）", session_id=session_id, error=str(exc))
            return []
        out: list[ChatMessage] = []
        for r in rows:
            # 分支 1：snapshot 行（带不带 type 都算）——展开 messages 数组整帧替换累计
            if isinstance(r.get("messages"), list):
                snapshot_messages: list[ChatMessage] = []
                for m in r["messages"]:
                    if not isinstance(m, dict):
                        continue
                    role = m.get("role")
                    if role == "system":
                        continue
                    if role not in ("user", "assistant", "tool"):
                        continue
                    content = m.get("content")
                    snapshot_messages.append(ChatMessage(
                        role=role,
                        content=content if content is not None else "",
                        tool_calls=m.get("tool_calls"),
                        tool_call_id=m.get("tool_call_id"),
                        name=m.get("name"),
                        reasoning_content=m.get("reasoning_content"),
                    ))
                out = snapshot_messages
                continue
            # 分支 2：per-message 行（PR1 之后的新写法）
            role = r.get("role")
            if role == "system":
                continue
            if role not in ("user", "assistant", "tool"):
                continue
            content = r.get("content")
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
                if m.role != "system"
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
