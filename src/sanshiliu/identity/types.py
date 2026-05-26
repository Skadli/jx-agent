"""人设快照数据类；core/ 下所有 md 按字母序拼成 system prompt。

PR2 新结构（2026-05-26）：
  persona/
    core/      <- PersonaSnapshot.sections 来源；按字母序拼接，全部常驻 system prompt
    modules/   <- 由 PersonaModuleLoader 读取，按需注入
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# 子目录名约定；core 下的 *.md 全部常驻拼接进 system prompt
CORE_DIRNAME: str = "core"
MODULES_DIRNAME: str = "modules"

# 段落分隔符；纯结构胶水，不属于"prompt 内容"
_SEPARATOR = "\n\n---\n\n"


@dataclass(frozen=True)
class PersonaSnapshot:
    """一次 core 人设快照：core/*.md 内容 + 各自 mtime + 加载时间。"""

    sections: dict[str, str]
    mtimes: dict[str, float]
    loaded_at: float
    persona_dir: Path
    # 按字母序固定的文件名列表；to_system_prompt 按此顺序拼接
    file_order: tuple[str, ...] = field(default_factory=tuple)

    def to_system_prompt(self) -> str:
        """按 file_order 拼接，仅插入纯结构分隔符。"""
        return _SEPARATOR.join(self.sections[name] for name in self.file_order if name in self.sections)

    def total_chars(self) -> int:
        """合并后字符数；用于 token 区间近似判断。"""
        return sum(len(s) for s in self.sections.values())

    def latest_mtime(self) -> float:
        return max(self.mtimes.values()) if self.mtimes else 0.0
