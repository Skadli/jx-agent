"""人设快照数据类；不在代码里塞任何 prompt 文本，全部内容来自 persona/*.md。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# 5 个人设 md 文件名 + 顺序——合并 system prompt 时按此顺序拼接
PERSONA_FILES: tuple[str, ...] = (
    "root.md",
    "personality.md",
    "beliefs.md",
    "style.md",
    "examples.md",
)

# 段落分隔符；纯结构胶水，不属于"prompt 内容"
_SEPARATOR = "\n\n---\n\n"


@dataclass(frozen=True)
class PersonaSnapshot:
    """一次快照：5 份 md 的原始内容 + 各自 mtime + 加载时间。"""

    sections: dict[str, str]
    mtimes: dict[str, float]
    loaded_at: float
    persona_dir: Path
    files: tuple[str, ...] = field(default=PERSONA_FILES)

    def to_system_prompt(self) -> str:
        """按 PERSONA_FILES 顺序拼接，仅插入纯结构分隔符。"""
        return _SEPARATOR.join(self.sections[name] for name in self.files)

    def total_chars(self) -> int:
        """合并后字符数；用于 token 区间近似判断。"""
        return sum(len(s) for s in self.sections.values())

    def latest_mtime(self) -> float:
        return max(self.mtimes.values()) if self.mtimes else 0.0
