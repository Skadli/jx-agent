"""会话快照 JSONL writer。

用途：把每轮对话的 user/assistant 消息按 JSON 行追加到磁盘，便于：
- 事后回放（debug / 翻车标注）
- Phase 7 自动记忆提取的数据源
- 与 sqlite llm_calls 表互为冗余审计

文件路径：``data_dir/logs/sessions/<session_id>.jsonl``
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from sanshiliu.foundation.logging import get_logger

_logger = get_logger(__name__)


class JsonlWriter:
    """每个会话独立一个 writer，append-only，进程退出时不需要显式 close。"""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir / "sessions"
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()  # 同一会话并发 append 时串行化

    def _path_for(self, session_id: str) -> Path:
        # 防路径穿越：只取非空字母数字_-
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")[:120] or "default"
        return self._base_dir / f"{safe}.jsonl"

    async def append(self, session_id: str, record: dict[str, Any]) -> None:
        """追加一行；调用方不需要再 await ``flush``——每次都 flush。"""
        path = self._path_for(session_id)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        async with self._lock:
            await asyncio.to_thread(self._append_sync, path, line)

    @staticmethod
    def _append_sync(path: Path, line: str) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
