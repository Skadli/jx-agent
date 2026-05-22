"""会话快照 JSONL writer；用于回放、记忆提取和冗余审计。"""

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
        # 防路径穿越，只保留安全字符
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
