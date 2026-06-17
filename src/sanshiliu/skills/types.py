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
    # 是否进常驻发现列表（listing）。缺省 True；元/基础设施 skill（dream/growth/gacha/skill-* 等）
    # 设 false 后不再每轮占 prompt，但仍可被 Skill 工具按名直调（匹配交给模型读 description）。
    discoverable: bool = True
    # CC 标准 frontmatter 字段：when_to_use 拼进 listing 描述（帮模型判断何时调）；
    # disable_model_invocation=true 的 skill 不进 listing、且 Skill 工具拒绝按名直调
    # （兑现"~/.claude 的 SKILL.md 拷过来直接生效"——此前这两个字段被静默忽略）。
    when_to_use: str = ""
    disable_model_invocation: bool = False
