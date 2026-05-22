"""skill 激活器；从 loader 拿全集 + 用 matchers 过滤命中项 + 拼成 prompt 增量。"""

from __future__ import annotations

from sanshiliu.foundation.logging import get_logger
from sanshiliu.skills.loader import SkillLoader
from sanshiliu.skills.matcher import KeywordMatcher, SemanticMatcher, SkillMatcher
from sanshiliu.skills.types import SkillDef

_logger = get_logger(__name__)

# 活跃 skills 拼到 system prompt 的分隔；纯结构胶水，非 prompt 内容
_SEP = "\n\n---\n\n"
# 活跃 skills 区段标题；UI 性质，让 LLM 知道这块是 skills 而不是 persona；
# 严格说也算 prompt 文本，未来可外置；目前保留是常量化的章节标记
_SECTION_HEADER = "# 活跃技能（active skills）"


class SkillActivator:
    """组合 loader 与 matchers；接口很薄。"""

    def __init__(
        self,
        loader: SkillLoader,
        *,
        matchers: list[SkillMatcher] | None = None,
    ) -> None:
        self._loader = loader
        self._matchers: list[SkillMatcher] = matchers or [KeywordMatcher(), SemanticMatcher()]

    def activate_for(self, user_text: str) -> list[SkillDef]:
        actives: list[SkillDef] = []
        for skill in self._loader.list():
            if any(m.match(skill, user_text) for m in self._matchers):
                actives.append(skill)
        if actives:
            _logger.info("skills 命中", ids=[s.id for s in actives])
        return actives

    def to_prompt_addition(self, actives: list[SkillDef]) -> str:
        if not actives:
            return ""
        parts: list[str] = [_SECTION_HEADER]
        for s in actives:
            parts.append(f"## {s.name}\n\n{s.body.strip()}")
        return _SEP.join(parts)
