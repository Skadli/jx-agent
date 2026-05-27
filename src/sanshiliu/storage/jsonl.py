"""会话 JSONL writer/reader；每会话独立 jsonl，append-only。

PR1（2026-05-27）：原本是 session-level snapshot（一行 = 整 session dump），
改为 per-message append（一行 = 一条 message），对齐 Claude Code transcript 格式。
配套 read_all() 让 ShortTermMemory 能在进程重启后 reload session。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from sanshiliu.foundation.logging import get_logger

_logger = get_logger(__name__)


class JsonlWriter:
    """每个会话独立一个 jsonl 文件，append-only。

    路径：<base_dir>/sessions/<safe_session_id>.jsonl
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir / "sessions"
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()  # 同一会话并发 append 时串行化

    def path_for(self, session_id: str) -> Path:
        """暴露给调用方的路径查询；reload 时也用同一规则定位文件。"""
        # 防路径穿越，只保留安全字符
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")[:120] or "default"
        return self._base_dir / f"{safe}.jsonl"

    async def append(self, session_id: str, record: dict[str, Any]) -> None:
        """追加一行；调用方不需要再 await ``flush``——每次都 flush。"""
        path = self.path_for(session_id)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        async with self._lock:
            await asyncio.to_thread(self._append_sync, path, line)

    async def read_all(self, session_id: str) -> list[dict[str, Any]]:
        """读回一个 session 的所有 jsonl 行；坏行跳过。

        路径不存在返空列表；和 Claude Code transcript 读法一致。
        """
        path = self.path_for(session_id)
        return await asyncio.to_thread(self._read_all_sync, path)

    @staticmethod
    def _append_sync(path: Path, line: str) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)

    @staticmethod
    def _read_all_sync(path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        out: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as exc:
                    _logger.warning("jsonl 坏行跳过", path=str(path), error=str(exc))
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
        return out
