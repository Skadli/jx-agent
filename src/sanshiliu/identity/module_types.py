"""persona module 公共类型；按需注入 system prompt 的"作品/风格库"片段。

与 SkillDef 故意不复用——skills 是 LLM 主动调用的工具，persona modules 是
直接注入 system prompt 的人格补充段。两者语义不同，强行复用会污染设计。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class PersonaModule:
    """单个 persona module 定义；id = 文件名（去 .md）；其余取自 frontmatter。"""

    id: str
    name: str
    description: str
    trigger_keywords: list[str] = field(default_factory=list)
    body: str = ""
    source: Path = field(default_factory=Path)
    mtime: float = 0.0
