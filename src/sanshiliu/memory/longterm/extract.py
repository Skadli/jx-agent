"""auto-memory extraction；每轮对话后异步调 LLM 提取候选记忆并落 memdir。"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from sanshiliu.foundation.errors import ConfigError, LLMError
from sanshiliu.foundation.logging import get_logger
from sanshiliu.llm.client import LLMClient
from sanshiliu.llm.router import LLMRouter
from sanshiliu.memory.longterm.memdir import write_memory_file
from sanshiliu.memory.types import MEMORY_TYPES, MemoryEntry, MemoryType

_logger = get_logger(__name__)

# 候选记忆置信度门槛；低于此值 drop（与 prompts/memory_extract.md 中约定一致）
_MIN_CONFIDENCE = 0.7
_EXTRACT_FILE = "memory_extract.md"
# 把对话片段送给 LLM 时单字段长度上限，防止 prompt 爆炸
_MAX_TURN_CHARS = 2000


def load_extract_instruction(prompts_dir: Path) -> str:
    """读 prompts/memory_extract.md；不存在抛 ConfigError 含字段名。"""
    path = prompts_dir / _EXTRACT_FILE
    if not path.is_file():
        raise ConfigError(
            f"缺少 {_EXTRACT_FILE}：{path}\n  解决：建立 prompts/{_EXTRACT_FILE}",
        )
    return path.read_text(encoding="utf-8").strip()


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    """LLM 可能返裸 JSON 也可能裹 markdown fence；剥两层取数组。"""
    cleaned = text.strip()
    m = _JSON_FENCE_RE.search(cleaned)
    if m:
        cleaned = m.group(1)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _coerce_entry(item: dict[str, Any]) -> MemoryEntry | None:
    """把 LLM 输出 dict 转 MemoryEntry；任何不合规字段一律 drop。"""
    name = item.get("name")
    description = item.get("description")
    if not isinstance(name, str) or not isinstance(description, str):
        return None
    metadata = item.get("metadata") or {}
    mtype_raw = metadata.get("type") if isinstance(metadata, dict) else None
    if not isinstance(mtype_raw, str) or mtype_raw not in MEMORY_TYPES:
        return None
    mtype: MemoryType = mtype_raw
    conf_raw = item.get("confidence")
    try:
        conf = float(conf_raw) if conf_raw is not None else None
    except (TypeError, ValueError):
        return None
    if conf is None or conf < _MIN_CONFIDENCE:
        return None
    body = str(item.get("body") or "").strip()
    # feedback / project 协议要求 body 含 **Why:** 段；缺失只观测、不丢弃（LLM 侧 prompt 已约束）
    if mtype in ("feedback", "project") and "**Why:**" not in body:
        _logger.info("extract: feedback/project 记忆缺 Why 段（容忍）", name=name.strip())
    return MemoryEntry(
        name=name.strip(),
        description=description.strip(),
        memory_type=mtype,
        body=body,
        confidence=conf,
        source="auto-extract",
        protected=False,
    )


class MemoryExtractor:
    """非阻塞 extract；engine 在每轮回复完后调度 background task。"""

    def __init__(
        self,
        *,
        llm: LLMClient | LLMRouter,
        memdir_root: Path,
        instruction: str,
    ) -> None:
        self._llm = llm
        self._memdir_root = memdir_root
        self._instruction = instruction

    def schedule(self, *, user_text: str, assistant_text: str, session_id: str) -> asyncio.Task[None]:
        """fire-and-forget；调用方不需要 await，失败也不抛。"""
        return asyncio.create_task(
            self._run(user_text=user_text, assistant_text=assistant_text, session_id=session_id),
            name=f"memory-extract-{session_id[:8]}",
        )

    async def _run(self, *, user_text: str, assistant_text: str, session_id: str) -> None:
        try:
            await self._extract_and_write(user_text, assistant_text, session_id)
        except Exception as exc:
            _logger.warning("memory extract 失败（不阻塞）", session_id=session_id, error=str(exc))

    async def _extract_and_write(self, user_text: str, assistant_text: str, session_id: str) -> int:
        turn = (
            f"[user] {user_text[:_MAX_TURN_CHARS]}\n\n[assistant] {assistant_text[:_MAX_TURN_CHARS]}"
        )
        messages = [
            {"role": "system", "content": self._instruction},
            {"role": "user", "content": turn},
        ]
        try:
            result = await self._llm.chat(
                messages=messages,
                session_id=session_id,
                channel="memory-extract-internal",
                temperature=0.2,
            )
        except LLMError as exc:
            _logger.info("extract LLM 调用失败", error=str(exc))
            return 0

        items = _parse_json_array(result.text)
        written = 0
        for item in items[:2]:  # 上限 2 条/轮，与 prompt 约定一致
            entry = _coerce_entry(item)
            if entry is None:
                continue
            try:
                write_memory_file(self._memdir_root, entry)
                written += 1
                _logger.info(
                    "extract 新增记忆",
                    name=entry.name, type=entry.memory_type, conf=entry.confidence,
                )
            except Exception as exc:
                _logger.warning("写 memdir 失败", error=str(exc))
        return written
