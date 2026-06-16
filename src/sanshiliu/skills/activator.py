"""skill 激活器；与 Claude Code SkillTool 一致——把所有 skills 的 name+description 列表注入 prompt，
让 LLM 自己判断何时调 Skill 工具拿正文。不再做关键字预匹配。"""

from __future__ import annotations

from sanshiliu.foundation.logging import get_logger
from sanshiliu.skills.loader import SkillLoader
from sanshiliu.skills.types import SkillDef

_logger = get_logger(__name__)

# 注入 system prompt 的章节标题；让 LLM 知道这块是 skills 列表
_SECTION_HEADER = "# 可用技能（available skills）"

# 与 Claude Code 一致：这张表只作"发现"用（discovery），真正的触发规则放在 `Skill` 工具的
# description 里（强制要求：匹配即在回答前先调）。这里只点一句核心规则 + 指向工具说明，避免把重
# 指令堆在 system prompt（模型决定调不调工具主要看工具自身 description，不是这段泛化文字）。
_SECTION_HINT = (
    "下面是当前可用的 skill（name — 用途），供你判断该调哪个。核心规则：用户的请求一旦匹配某个 skill，"
    "就**先调 `Skill` 工具拿正文、再就该任务作答**（强制要求，别先凭记忆答完才补调）；细则见 Skill 工具说明。"
)


class SkillActivator:
    """组合 loader 与 lookup；不再持有 matchers。"""

    def __init__(self, loader: SkillLoader) -> None:
        self._loader = loader

    def list_all(self) -> list[SkillDef]:
        """全量 skill 定义；hot reload 时实时反映磁盘状态由 loader 控制。"""
        return self._loader.list()

    def lookup(self, skill_id: str) -> SkillDef | None:
        """找单个 skill：先按 id 精确命中，再回退按 name 匹配。

        两轮分开是因为：缺 name 的 skill 现在会用目录名兜底 name(=id)，若另一 skill 的
        frontmatter name 恰好等于本 skill 的目录名，单轮 `id==x or name==x` 会按遍历序静默命中
        错的那个。id 优先保证"按 id 调"永远先中自己；name 匹配仍保留（listing 给模型看的是 name）。
        """
        skills = self._loader.list()
        for s in skills:
            if s.id == skill_id:
                return s
        for s in skills:
            if s.name == skill_id:
                return s
        return None

    def list_for_prompt(self) -> str:
        """把全量 skills 拼成 system prompt 的 listing 段落；无 skill 时返回空串。"""
        skills = self._loader.list()
        if not skills:
            return ""
        lines: list[str] = [_SECTION_HEADER, "", _SECTION_HINT, ""]
        for s in skills:
            # 与 Claude Code 一致：listing 只放 name + description 首行（CC 的 listing 也不含
            # keywords，且明确避免往发现列表堆冗余文本——浪费 turn-1 cache_creation token、不提升
            # 命中率）。keywords 不进这里，仅供 dashboard / structure / 成长安装搜索消费。
            desc_first = s.description.strip().splitlines()[0] if s.description.strip() else ""
            lines.append(f"- **{s.name}** — {desc_first}")
        _logger.debug("skills listing 已构造", count=len(skills))
        return "\n".join(lines)
