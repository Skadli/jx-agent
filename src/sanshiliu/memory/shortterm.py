"""短期记忆；context.manager 的薄包装 + 会话快照 jsonl 导出。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

from sanshiliu.foundation.logging import get_logger
from sanshiliu.storage.jsonl import JsonlWriter

if TYPE_CHECKING:
    from sanshiliu.engine.session import Session

_logger = get_logger(__name__)


class ShortTermMemory:
    """短期记忆 = 当前会话的 messages + budget；这里只暴露快照导出 API。"""

    def __init__(self, base_dir: Path) -> None:
        self._writer = JsonlWriter(base_dir)

    async def snapshot(self, session: Session) -> None:
        """把当前 session 全量导出为一行 jsonl；用于事后回放或离线分析。"""
        record = {
            "ts": int(time.time() * 1000),
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
