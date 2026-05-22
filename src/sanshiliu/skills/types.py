"""skills 公共类型；与 Claude SKILL.md 协议对齐。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SkillDef:
    """单个 skill 定义；id = 目录名；name/description/keywords 取自 frontmatter；body = 正文。"""

    id: str
    name: str
    description: str
    keywords: list[str] = field(default_factory=list)
    body: str = ""
    source: Path = field(default_factory=Path)
    priority: int = 0  # 0=project, 1=global, 2=repo
