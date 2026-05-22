"""L6 技能层；SKILL.md 协议加载 + 关键词/语义匹配 + 注入 system prompt。"""

from sanshiliu.skills.activator import SkillActivator
from sanshiliu.skills.loader import SkillLoader
from sanshiliu.skills.matcher import KeywordMatcher, SemanticMatcher, SkillMatcher
from sanshiliu.skills.types import SkillDef

__all__ = [
    "KeywordMatcher",
    "SemanticMatcher",
    "SkillActivator",
    "SkillDef",
    "SkillLoader",
    "SkillMatcher",
]
