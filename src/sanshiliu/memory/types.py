"""memory 公共类型；与 Claude memdir 协议一致的 4 类 + frontmatter 字段。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# 4 类与 Claude 一致：user 偏好 / feedback 反馈 / project 项目 / reference 参考
MemoryType = Literal["user", "feedback", "project", "reference"]
MEMORY_TYPES: tuple[MemoryType, ...] = ("user", "feedback", "project", "reference")

# MEMORY.md 索引最大行数；超过截断并加 WARNING（prd 7-V5）
MEMORY_INDEX_MAX_LINES = 200


@dataclass(frozen=True)
class MemoryEntry:
    """单条记忆；frontmatter + body 拼装而成。"""

    name: str
    description: str
    memory_type: MemoryType
    body: str = ""
    source: str | None = None
    confidence: float | None = None
    protected: bool = False
    file_path: Path = field(default_factory=Path)
    wiki_links: list[str] = field(default_factory=list)

    def index_line(self) -> str:
        """MEMORY.md 索引行格式（Claude 协议）：- [name](file.md) — description（一行一条）。"""
        desc = self.description.strip().replace("\n", " ")[:120]
        file_name = self.file_path.name or f"{self.memory_type}_{self.name}.md"
        return f"- [{self.name}]({file_name}) — {desc}"


@dataclass(frozen=True)
class MemorySnapshot:
    """memdir 加载的全集 + 索引文本。"""

    entries: list[MemoryEntry]
    index_text: str
    memdir_root: Path

    def by_type(self) -> dict[MemoryType, list[MemoryEntry]]:
        out: dict[MemoryType, list[MemoryEntry]] = {t: [] for t in MEMORY_TYPES}
        for e in self.entries:
            out[e.memory_type].append(e)
        return out
