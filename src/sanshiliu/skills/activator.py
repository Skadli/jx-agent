"""skill 激活器；与 Claude Code SkillTool 一致——把所有 skills 的 name+description 列表注入 prompt，
让 LLM 自己判断何时调 Skill 工具拿正文。

此外按 user_text 做一次轻量关键词预判（pick）：命中某 skill 时，在 listing 顶部追加一条本轮强制
指令（hit_directive），把"先调 Skill 再作答"从工具 description 的弱信号提升为带具体 skill 名的
system 指令——与 persona module 的引擎侧自动注入对称，给弱遵从模型补一条不靠"自觉读工具
description"的兜底通路。预判只产"提示"、不注入正文（正文仍由 LLM 调 Skill 工具拿，保持轻 system prompt）。"""

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

# 关键词预判命中某 skill 时，注入 listing 顶部的本轮强制指令模板。把 Skill 工具 description 里那条
# BLOCKING（"回答前先调"）提升为带具体 skill 名、占 listing 段首的 system 指令——弱遵从模型（DeepSeek 等）
# 对工具 description 的依从度低，这条带名的硬提示能显著提高命中。只在 pick() 命中那轮注入，不命中不变。
_HIT_DIRECTIVE_TPL = (
    "⚠️ 本轮用户请求已命中 skill **{name}**（引擎按关键词预判）。强制要求：在做任何其它事"
    "（尤其用 bash / web_search 自己实现）之前，**先调 `Skill(\"{name}\")` 取正文、再据此完成本轮任务**；"
    "别跳过，也别只在嘴上提到却不调。"
)


class SkillActivator:
    """组合 loader + lookup + 轻量关键词预判（pick）。"""

    def __init__(self, loader: SkillLoader) -> None:
        self._loader = loader

    def list_all(self) -> list[SkillDef]:
        """全量 skill 定义；hot reload 时实时反映磁盘状态由 loader 控制。"""
        return self._loader.list()

    def pick(self, user_text: str) -> SkillDef | None:
        """关键词命中算法：substring 匹配，命中数最多的赢；同分按 id 字母序 tie-break。

        照搬 PersonaModuleActivator.pick（同一套语义），消费 SkillDef.keywords（loader 已解析、
        此前仅供 dashboard/structure/成长安装搜索消费）。小写比较；命中 0 个返回 None。
        仅作"本轮该提醒调哪个 skill"的预判——不注入正文，正文仍由 LLM 调 Skill 工具拿。
        无 keywords 的 skill 永远 score=0、不会被命中，等价退回纯 listing 行为（向后兼容）。
        """
        if not user_text:
            return None
        text_lc = user_text.lower()
        scored: list[tuple[int, str, SkillDef]] = []
        for s in self._loader.list():
            score = sum(1 for kw in s.keywords if kw and kw.lower() in text_lc)
            if score > 0:
                scored.append((score, s.id, s))
        if not scored:
            return None
        # 命中数倒序 + id 正序 → 稳定 tie-break
        scored.sort(key=lambda t: (-t[0], t[1]))
        chosen = scored[0][2]
        _logger.debug(
            "skill 关键词命中",
            skill=chosen.id, score=scored[0][0], total_candidates=len(scored),
        )
        return chosen

    def hit_directive(self, skill: SkillDef) -> str:
        """命中某 skill 时，给本轮 prompt 顶部追加的强制指令（含具体 skill 名）。"""
        return _HIT_DIRECTIVE_TPL.format(name=skill.name)

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
