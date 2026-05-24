"""skill 激活器；与 Claude Code SkillTool 一致——把所有 skills 的 name+description 列表注入 prompt，
让 LLM 自己判断何时调 Skill 工具拿正文。不再做关键字预匹配。"""

from __future__ import annotations

from sanshiliu.foundation.logging import get_logger
from sanshiliu.skills.loader import SkillLoader
from sanshiliu.skills.types import SkillDef

_logger = get_logger(__name__)

# 注入 system prompt 的章节标题；让 LLM 知道这块是 skills 列表
_SECTION_HEADER = "# 可用技能（available skills）"

# 说明 LLM 如何用列表里的 skill
_SECTION_HINT = (
    "若用户请求与下列某 skill 的 description 描述场景相符，调 `Skill` 工具，"
    "参数 `skill` 填该项的 name；一轮对话不要重复调用同一个 skill。"
)


class SkillActivator:
    """组合 loader 与 lookup；不再持有 matchers。"""

    def __init__(self, loader: SkillLoader) -> None:
        self._loader = loader

    def list_all(self) -> list[SkillDef]:
        """全量 skill 定义；hot reload 时实时反映磁盘状态由 loader 控制。"""
        return self._loader.list()

    def lookup(self, skill_id: str) -> SkillDef | None:
        """按 id 找单个 skill；id 来自目录名 / frontmatter.name（loader 用目录名作 id）。"""
        for s in self._loader.list():
            if s.id == skill_id or s.name == skill_id:
                return s
        return None

    def list_for_prompt(self) -> str:
        """把全量 skills 拼成 system prompt 的 listing 段落；无 skill 时返回空串。"""
        skills = self._loader.list()
        if not skills:
            return ""
        lines: list[str] = [_SECTION_HEADER, "", _SECTION_HINT, ""]
        for s in skills:
            # 单行 description 优先；多行的截到第一段，避免一份 skill 占满 prompt
            desc_first = s.description.strip().splitlines()[0] if s.description.strip() else ""
            lines.append(f"- **{s.name}** — {desc_first}")
        _logger.debug("skills listing 已构造", count=len(skills))
        return "\n".join(lines)
