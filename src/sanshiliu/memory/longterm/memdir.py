"""memdir 加载与写入；扫描 *.md 文件 → frontmatter → MemoryEntry；维护 MEMORY.md 索引。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from sanshiliu.foundation.frontmatter import parse
from sanshiliu.foundation.logging import get_logger
from sanshiliu.memory.longterm.wiki_link import parse_links
from sanshiliu.memory.types import (
    MEMORY_INDEX_MAX_LINES,
    MEMORY_TYPES,
    MemoryEntry,
    MemorySnapshot,
    MemoryType,
)

_logger = get_logger(__name__)

# MEMORY.md 文件名固定；超出 200 行追加在头部的 WARNING 标记
_INDEX_FILE = "MEMORY.md"
_WARNING_HEADER = (
    "<!-- WARNING: 索引超出 200 行已被截断；旧条目仍在磁盘但需手工合并 -->\n"
)


def _resolve_type(raw: Any) -> MemoryType | None:
    """frontmatter 中 metadata.type 或顶层 type 字段，归一化到 MEMORY_TYPES。"""
    if isinstance(raw, dict):
        raw = raw.get("type")
    if not isinstance(raw, str):
        return None
    t = raw.strip().lower()
    return t if t in MEMORY_TYPES else None


def _entry_from_file(path: Path) -> MemoryEntry | None:
    """读单个 md → MemoryEntry；缺必填字段或类型非法时返回 None 并记日志。"""
    try:
        parsed = parse(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _logger.warning("memdir 文件解析失败，跳过", path=str(path), error=str(exc))
        return None
    fm = parsed.frontmatter
    name = fm.get("name")
    description = fm.get("description")
    if not isinstance(name, str) or not isinstance(description, str):
        _logger.warning("memdir 缺 name/description，跳过", path=str(path))
        return None
    mtype = _resolve_type(fm.get("metadata")) or _resolve_type(fm.get("type"))
    if mtype is None:
        _logger.warning("memdir metadata.type 非法或缺失，跳过", path=str(path))
        return None
    confidence = fm.get("confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = None
    return MemoryEntry(
        name=name.strip(),
        description=description.strip(),
        memory_type=mtype,
        body=parsed.body,
        source=fm.get("source") if isinstance(fm.get("source"), str) else None,
        confidence=confidence,
        protected=bool(fm.get("protected", False)),
        file_path=path,
        wiki_links=[n for n, _ in parse_links(parsed.body)],
    )


class MemdirLoader:
    """单目录 memdir 加载器；扫所有 *.md（除 MEMORY.md 自身）拼 MemorySnapshot。"""

    def __init__(self, memdir_root: Path) -> None:
        self._root = memdir_root
        self._cache: MemorySnapshot | None = None

    @property
    def root(self) -> Path:
        return self._root

    def load(self) -> MemorySnapshot:
        entries: list[MemoryEntry] = []
        if self._root.is_dir():
            for path in sorted(self._root.glob("*.md")):
                if path.name == _INDEX_FILE:
                    continue
                entry = _entry_from_file(path)
                if entry is not None:
                    entries.append(entry)
        index_text = self._read_index()
        snap = MemorySnapshot(entries=entries, index_text=index_text, memdir_root=self._root)
        self._cache = snap
        _logger.info("memdir 加载", count=len(entries), root=str(self._root))
        return snap

    def get(self) -> MemorySnapshot:
        return self._cache if self._cache is not None else self.load()

    def invalidate(self) -> None:
        self._cache = None

    def _read_index(self) -> str:
        path = self._root / _INDEX_FILE
        if not path.is_file():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            _logger.warning("MEMORY.md 读失败", path=str(path), error=str(exc))
            return ""


def append_index_line(memdir_root: Path, line: str) -> None:
    """追加一行到 MEMORY.md；超过 200 行截断并加 WARNING（prd 7-V5）。"""
    memdir_root.mkdir(parents=True, exist_ok=True)
    path = memdir_root / _INDEX_FILE
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = [ln for ln in existing.splitlines() if ln.strip() and not ln.startswith("<!--")]
    lines.append(line.rstrip())
    if len(lines) > MEMORY_INDEX_MAX_LINES:
        kept = lines[-MEMORY_INDEX_MAX_LINES:]
        body = _WARNING_HEADER + "\n".join(kept) + "\n"
    else:
        body = "\n".join(lines) + "\n"
    path.write_text(body, encoding="utf-8")


def write_memory_file(memdir_root: Path, entry: MemoryEntry, body: str | None = None) -> Path:
    """把一条 MemoryEntry 落盘 + 更新 MEMORY.md 索引；返回写入的文件路径。"""
    memdir_root.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in entry.name if c.isalnum() or c in "-_") or "untitled"
    file_name = f"{entry.memory_type}_{safe}_{int(time.time())}.md"
    file_path = memdir_root / file_name
    fm_lines = [
        "---",
        f"name: {entry.name}",
        f"description: {entry.description}",
        "metadata:",
        f"  type: {entry.memory_type}",
    ]
    if entry.confidence is not None:
        fm_lines.append(f"confidence: {entry.confidence}")
    if entry.source:
        fm_lines.append(f"source: {entry.source}")
    if entry.protected:
        fm_lines.append("protected: true")
    fm_lines.append("---")
    full = "\n".join(fm_lines) + "\n\n" + (body or entry.body or "").strip() + "\n"
    file_path.write_text(full, encoding="utf-8")
    append_index_line(memdir_root, entry.index_line())
    return file_path
