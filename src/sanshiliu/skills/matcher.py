"""skill 匹配器；当前只实现关键词子串匹配，语义匹配留接口给 Phase 7+。"""

from __future__ import annotations

from typing import Protocol

from sanshiliu.foundation.logging import get_logger
from sanshiliu.skills.types import SkillDef

_logger = get_logger(__name__)


class SkillMatcher(Protocol):
    """匹配协议；match() 返回 True 表示该 skill 被触发。"""

    def match(self, skill: SkillDef, user_text: str) -> bool: ...


class KeywordMatcher:
    """子串匹配；keywords 任一出现在 user_text 中即命中。"""

    def match(self, skill: SkillDef, user_text: str) -> bool:
        text = user_text.lower()
        for kw in skill.keywords:
            if not kw:
                continue
            if kw.lower() in text:
                _logger.debug("skill 命中关键词", skill=skill.id, kw=kw)
                return True
        return False


class SemanticMatcher:
    """语义匹配占位；无 embedding 配置时永远返回 False（不影响 keyword 路径）。"""

    def __init__(self, embedding_fn: object | None = None) -> None:
        # embedding_fn 是一个 async callable: (str) -> list[float]；None 时关闭
        self._embedding_fn = embedding_fn

    def match(self, skill: SkillDef, user_text: str) -> bool:
        if self._embedding_fn is None:
            return False
        # Phase 6 不接 embedding；保留接口便于 Phase 7+ 实施
        return False
