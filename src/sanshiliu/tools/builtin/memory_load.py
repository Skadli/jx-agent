"""LoadMemory：同名分流——UUID/"recent" 查历史 session；slug 查 memdir。

设计取舍：
- 不走 PathGuard：memdir 目录是 agent 自治领域；session jsonl 同样是内部数据；
- 不走 PermissionManager：纯读操作，自动放行；
- 找不到 memdir 条目时返 is_error，附前 10 个 name 提示 LLM 选择正确条目。
- session 查询失败（无 row / jsonl 空）同样返 is_error。

2026-05-27 扩展：name 字段同名分流——
  - UUID 正则匹配 → 走 session 路径，加 tail 参数（默认 10，上限 50）；
  - name=="recent" magic → 找当前 channel+user_id 最近一个其他 session；
  - 否则 → 原 memdir 查询路径（向后兼容）。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sanshiliu.engine.types import ChatMessage
from sanshiliu.foundation.logging import get_logger
from sanshiliu.memory.longterm.memdir import MemdirLoader
from sanshiliu.memory.shortterm import ShortTermMemory
from sanshiliu.memory.types import MemoryEntry
from sanshiliu.storage.db import Database
from sanshiliu.tools.types import ToolDef, ToolResult, _check_required_fields

_logger = get_logger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
# tail 参数上限：防爆 token
_TAIL_MAX = 50
# 单条 message 渲染上限：避免某条 tool_result 把整段冲爆
_PER_MSG_CHARS = 200


def _render_memdir(entry: MemoryEntry) -> str:
    """frontmatter（仅常用字段）+ \\n\\n + body 拼回工具结果文本。"""
    lines = [
        "---",
        f"name: {entry.name}",
        f"type: {entry.memory_type}",
        f"description: {entry.description}",
    ]
    if entry.source:
        lines.append(f"source: {entry.source}")
    if entry.confidence is not None:
        lines.append(f"confidence: {entry.confidence}")
    if entry.protected:
        lines.append("protected: true")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + (entry.body or "").strip()


def _render_message_line(msg: ChatMessage) -> str:
    """把一条 message 渲成 prompt 友好的单行/多行文本；超 _PER_MSG_CHARS 截断。"""
    # content 可能是 str 或 list（多模态）；统一摊成 text
    if isinstance(msg.content, str):
        text = msg.content
    else:
        parts: list[str] = []
        for part in msg.content:
            if isinstance(part, dict) and part.get("type") == "text":
                t = part.get("text")
                if isinstance(t, str):
                    parts.append(t)
        text = " ".join(parts)
    text = text.strip()
    # tool_calls 摘要 / tool 消息名
    suffix = ""
    if msg.tool_calls:
        names = [
            str(tc.get("function", {}).get("name") or "?") for tc in msg.tool_calls
        ]
        suffix = f" [tool_calls: {', '.join(names)}]"
    if msg.role == "tool" and msg.name:
        suffix = f" (tool: {msg.name})" + suffix
    body = text if text else "（空内容）"
    if len(body) > _PER_MSG_CHARS:
        body = body[:_PER_MSG_CHARS] + "…"
    return f"[role={msg.role}] {body}{suffix}"


def _render_session(
    *,
    target_sid: str,
    row: dict[str, Any],
    all_msgs: list[ChatMessage],
    tail_msgs: list[ChatMessage],
) -> str:
    """渲染 session markdown：短 uuid header + compact_summary + recent N。"""
    short_sid = target_sid[:8] + "..."
    last_ts = row.get("last_active_at")
    ts_str = ""
    if isinstance(last_ts, int):
        ts_str = datetime.fromtimestamp(last_ts / 1000).strftime("%Y-%m-%d %H:%M")
    summary = (row.get("compact_summary") or "").strip()
    lines: list[str] = []
    lines.append(
        f"## Session {short_sid} (channel={row.get('channel')}, last_active={ts_str})"
    )
    lines.append("")
    lines.append("### Earlier (compact_summary)")
    lines.append(summary if summary else "（无压缩摘要）")
    lines.append("")
    lines.append(
        f"### Recent {len(tail_msgs)} messages (of {len(all_msgs)} total)"
    )
    for m in tail_msgs:
        lines.append(_render_message_line(m))
    return "\n".join(lines)


async def _load_session_path(
    *,
    definition: ToolDef,
    name: str,
    tail: int,
    current_session_id: str,
    short_term: ShortTermMemory,
    db: Database,
) -> ToolResult:
    """name=='recent' 或 UUID 时调；返渲染好的 markdown 文本 ToolResult。"""
    # 1) 解析 target_sid
    if name == "recent":
        if not current_session_id:
            return ToolResult(
                "", definition.name,
                "无法确定当前 session（current_session_id 为空）",
                is_error=True,
            )
        current_row = await db.get_session(current_session_id)
        if current_row is None:
            return ToolResult(
                "", definition.name,
                "无法确定当前 session 元数据",
                is_error=True,
            )
        ch = str(current_row.get("channel") or "")
        uid_raw = current_row.get("user_id")
        uid = str(uid_raw) if uid_raw is not None else None
        # 用 list_recent_sessions_for_prompt(limit=1, exclude_id=current) 拿首条
        rows = await db.list_recent_sessions_for_prompt(
            channel=ch, user_id=uid, limit=1, exclude_id=current_session_id,
        )
        if not rows:
            return ToolResult(
                "", definition.name,
                "没有可查的历史 session（同 channel + user_id 下只有当前 session）",
                is_error=True,
            )
        target_sid = str(rows[0]["id"])
    else:
        target_sid = name

    # 2) 拿 sessions row
    target_row = await db.get_session(target_sid)
    if target_row is None:
        return ToolResult(
            "", definition.name,
            f"session 不存在：{target_sid}",
            is_error=True,
        )

    # 3) reload jsonl
    msgs = await short_term.reload(target_sid)
    if not msgs:
        return ToolResult(
            "", definition.name,
            f"session jsonl 为空或不存在：{target_sid}",
            is_error=True,
        )

    # 4) tail 截断 + 渲染
    take = min(max(tail, 1), _TAIL_MAX)
    tail_msgs = msgs[-take:]
    text = _render_session(
        target_sid=target_sid, row=target_row,
        all_msgs=msgs, tail_msgs=tail_msgs,
    )
    _logger.info(
        "LoadMemory 拉 session",
        target_sid=target_sid, tail=len(tail_msgs), total=len(msgs),
    )
    return ToolResult("", definition.name, text)


class LoadMemoryTool:
    """Tool 协议实现；持有 memdir_loader + short_term + db 闭包。

    与 FunctionTool 不同——execute 接收 session_id 后能转给 _load_session_path。
    """

    def __init__(
        self,
        definition: ToolDef,
        memdir_loader: MemdirLoader,
        short_term: ShortTermMemory | None,
        db: Database | None,
    ) -> None:
        self._def = definition
        self._memdir_loader = memdir_loader
        self._short_term = short_term
        self._db = db

    @property
    def definition(self) -> ToolDef:
        return self._def

    async def validate(self, args: dict[str, Any]) -> str | None:
        if (err := _check_required_fields(args, self._def.input_schema)) is not None:
            return err
        # tail 类型 & 范围校验提前（execute 里也会判，但 validate 失败省得绕一圈）
        tail_raw = args.get("tail")
        if tail_raw is not None:
            try:
                tail = int(tail_raw)
            except (TypeError, ValueError):
                return f"tail 必须是整数：{tail_raw}"
            if tail < 1 or tail > _TAIL_MAX:
                return f"tail 必须在 1-{_TAIL_MAX} 之间，当前 {tail}"
        return None

    async def execute(
        self, args: dict[str, Any], *, session_id: str = "",
    ) -> ToolResult:
        name = str(args.get("name") or "").strip()
        if not name:
            return ToolResult(
                "", self._def.name, "参数 name 不能为空", is_error=True,
            )
        # tail 默认 10；validate 已确保类型范围
        tail_raw = args.get("tail")
        tail = 10 if tail_raw is None else int(tail_raw)

        # 分流：UUID / "recent" → session 路径；否则 → memdir
        is_session_path = (name == "recent") or bool(_UUID_RE.match(name))
        if is_session_path:
            if self._short_term is None or self._db is None:
                return ToolResult(
                    "", self._def.name,
                    "session 查询不可用：short_term/db 未装配",
                    is_error=True,
                )
            return await _load_session_path(
                definition=self._def, name=name, tail=tail,
                current_session_id=session_id,
                short_term=self._short_term, db=self._db,
            )

        # memdir 路径（向后兼容）
        snap = self._memdir_loader.get()
        for entry in snap.entries:
            if entry.name == name:
                _logger.info("LoadMemory 命中 memdir", name=name, type=entry.memory_type)
                return ToolResult("", self._def.name, _render_memdir(entry))
        available = ", ".join(e.name for e in snap.entries[:10]) or "（无）"
        return ToolResult(
            "", self._def.name,
            f"未找到记忆: {name}。可用条目: {available}",
            is_error=True,
        )


def build_load_memory_tool(
    definition: ToolDef,
    memdir_loader: MemdirLoader,
    short_term: ShortTermMemory | None = None,
    db: Database | None = None,
) -> LoadMemoryTool:
    """构造 LoadMemoryTool；short_term/db 缺省时只支持 memdir 查询（不报错地降级）。"""
    return LoadMemoryTool(definition, memdir_loader, short_term, db)
