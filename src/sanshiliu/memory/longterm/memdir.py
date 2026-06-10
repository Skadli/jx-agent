"""memdir 加载与写入；扫描 *.md 文件 → frontmatter → MemoryEntry；维护 MEMORY.md 索引。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from sanshiliu.foundation.frontmatter import parse
from sanshiliu.foundation.logging import get_logger
from sanshiliu.memory.longterm.wiki_link import parse_links
from sanshiliu.memory.types import (
    MEMORY_APPLIES,
    MEMORY_TYPES,
    MemoryApply,
    MemoryEntry,
    MemorySnapshot,
    MemoryType,
)

_logger = get_logger(__name__)

# MEMORY.md 文件名固定
_INDEX_FILE = "MEMORY.md"
_INDEX_HEADER = """<!-- 本文件由代码自动维护：扫描 memdir/*.md 重建，勿手改。 -->

# MEMORY.md

这是长期记忆索引，只列 name、文件、类型、标志和摘要，不包含完整正文。

- `metadata.apply=always`：正文会每轮直接注入 system prompt，属于必须遵守的长期偏好/行为规则。
- 其他条目：只进入索引；需要具体正文时调用 `LoadMemory({"name":"<name>"})`。
- 修改记忆请编辑对应 `memdir/*.md` 文件；本文件可由代码重建。

## 索引

"""


def _resolve_type(raw: Any) -> MemoryType | None:
    """frontmatter 中 metadata.type 或顶层 type 字段，归一化到 MEMORY_TYPES。"""
    if isinstance(raw, dict):
        raw = raw.get("type")
    if not isinstance(raw, str):
        return None
    t = raw.strip().lower()
    return t if t in MEMORY_TYPES else None


def _resolve_apply(raw: Any) -> MemoryApply | None:
    """frontmatter 中 metadata.apply 或顶层 apply 字段，归一化到支持值。"""
    if isinstance(raw, dict):
        raw = raw.get("apply")
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    return value if value in MEMORY_APPLIES else None


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
    apply = _resolve_apply(fm.get("metadata")) or _resolve_apply(fm.get("apply"))
    if mtype is None:
        _logger.warning(
            "memdir 跳过：frontmatter 缺 metadata.type"
            "（应为 user/feedback/project/reference 之一）",
            path=str(path),
        )
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
        apply=apply,
        protected=bool(fm.get("protected", False)),
        file_path=path,
        wiki_links=[n for n, _ in parse_links(parsed.body)],
    )


def _scan_entries(root: Path) -> list[MemoryEntry]:
    """扫 memdir 目录所有 *.md（除 MEMORY.md 自身）→ MemoryEntry 列表，跳过解析失败的。"""
    entries: list[MemoryEntry] = []
    if root.is_dir():
        for path in sorted(root.glob("*.md")):
            if path.name == _INDEX_FILE:
                continue
            entry = _entry_from_file(path)
            if entry is not None:
                entries.append(entry)
    return entries


class MemdirLoader:
    """单目录 memdir 加载器；扫所有 *.md（除 MEMORY.md 自身）拼 MemorySnapshot。"""

    def __init__(self, memdir_root: Path) -> None:
        self._root = memdir_root
        self._cache: MemorySnapshot | None = None

    @property
    def root(self) -> Path:
        return self._root

    def load(self) -> MemorySnapshot:
        entries = _scan_entries(self._root)
        index_text = self._read_index()
        snap = MemorySnapshot(entries=entries, index_text=index_text, memdir_root=self._root)
        self._cache = snap
        # 膨胀告警：超阈值仅日志提醒，不阻塞启动；提醒用户跑 /memory consolidate
        if len(entries) >= 50 or len(index_text.splitlines()) >= 150:
            _logger.warning(
                "memdir 已积累较多，建议跑 /memory consolidate 整理",
                entries=len(entries),
                index_lines=len(index_text.splitlines()),
            )
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


def format_index_lines(entries: list[MemoryEntry]) -> str:
    """从真实记忆条目生成权威索引文本（不依赖手维护的 MEMORY.md，避免漂移）。

    每条一行：`- [name](file) · type[, 标志] — description`，含 name/链接/metadata/描述。
    """
    lines: list[str] = []
    for e in entries:
        file_name = e.file_path.name or f"{e.memory_type}_{e.name}.md"
        meta: str = e.memory_type
        if e.apply is not None:
            meta += f", apply={e.apply}"
        if e.protected:
            meta += ", protected"
        if e.confidence is not None:
            meta += f", confidence={e.confidence:g}"
        desc = e.description.strip().replace("\n", " ")
        lines.append(f"- [{e.name}]({file_name}) · {meta} — {desc}")
    return "\n".join(lines)


def format_always_memory_block(entries: list[MemoryEntry]) -> str:
    """把 apply=always 的记忆正文渲染成直接注入 system prompt 的规则块。"""
    always = [e for e in entries if e.apply == "always" and e.body.strip()]
    if not always:
        return ""
    parts = [
        "# 长期记忆 · 必须遵守",
        "以下条目已标记 `metadata.apply=always`。它们不是可选参考，"
        "而是本轮回复必须直接遵守的用户偏好、称呼、风格或行为规则。",
    ]
    for entry in always:
        parts.append(
            "\n".join(
                [
                    f"## {entry.name} ({entry.memory_type})",
                    f"摘要：{entry.description}",
                    "",
                    entry.body.strip(),
                ]
            )
        )
    return "\n\n".join(parts)


def rebuild_index_file(memdir_root: Path, entries: list[MemoryEntry] | None = None) -> None:
    """从真实记忆文件重建 MEMORY.md（权威，修复手维护漂移）。entries 缺省则现扫目录。"""
    memdir_root.mkdir(parents=True, exist_ok=True)
    if entries is None:
        entries = _scan_entries(memdir_root)
    body = format_index_lines(entries)
    path = memdir_root / _INDEX_FILE
    path.write_text(_INDEX_HEADER + body + ("\n" if body else ""), encoding="utf-8")


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
    if entry.apply is not None:
        fm_lines.append(f"  apply: {entry.apply}")
    if entry.confidence is not None:
        fm_lines.append(f"confidence: {entry.confidence}")
    if entry.source:
        fm_lines.append(f"source: {entry.source}")
    if entry.protected:
        fm_lines.append("protected: true")
    fm_lines.append("---")
    full = "\n".join(fm_lines) + "\n\n" + (body or entry.body or "").strip() + "\n"
    file_path.write_text(full, encoding="utf-8")
    # 重扫全部条目重建索引（权威，含刚写入的这条 + 修复历史漂移），替代盲目 append。
    rebuild_index_file(memdir_root)
    return file_path
