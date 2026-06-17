"""skill 激活器；与 Claude Code SkillTool 一致——把可用 skills 的 name+description 列成清单，
让 LLM 按 description 自己语义判断该调哪个、何时调，再用 Skill 工具拿正文。

不做引擎侧关键词匹配（keywords 仅作 dashboard / 搜索的元数据，不参与触发）——匹配交给模型读
description：天然跨语言（"加密"↔cryptography 模型本来就懂）、对新装 skill 零配置即生效，与 CC 一致。
清单由 channel 在每轮作为高 recency 的 <system-reminder> 贴着用户消息注入（见 to_openai_messages），
触发规则（匹配即回答前先调 Skill）随之吃到 recency。"""

from __future__ import annotations

from sanshiliu.foundation.logging import get_logger
from sanshiliu.skills.loader import SkillLoader
from sanshiliu.skills.types import SkillDef

_logger = get_logger(__name__)

# <system-reminder> 段标题
_SECTION_HEADER = "# 可用技能（available skills）"

# 触发规则——随清单一起以 <system-reminder> 贴用户消息注入（高 recency），所以写成强规则、不再是
# 埋在 system 中段的软提示。与 CC 的 Skill 工具 BLOCKING 一致：匹配即回答前先调。显式压过"简短直出"
# 的近因反射（否则 system 末尾的长度锚会把模型推向"直接答"）。
_SECTION_RULE = (
    "规则（优先于「简短直出」的习惯）：用户这条请求只要落在下面某个 skill 的用途范围内，**必须在"
    "回答任何内容、或自己用 bash / web_search 动手之前，先调用 `Skill` 工具取回该 skill 的正文**，"
    "再据此完成。按 description 判断该不该调、别等用户点名；调完工具再按你的风格简短作答。"
)

# listing 单条 description 的上限（字符）。对齐 Claude Code 的 MAX_LISTING_DESC_CHARS=250：description
# 现在是"模型据以匹配"的唯一信号，别砍太狠丢掉 whenToUse 语义；但第三方 SKILL.md 数百字的长触发文也
# 不必整段带，250 足够表达用途。超限截断 + 省略号。
_MAX_LISTING_DESC_CHARS = 250


class SkillActivator:
    """组合 loader + lookup；不做关键词预判，匹配交给模型读 description（与 CC 一致）。"""

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
        """把可发现 skills 拼成 system prompt 的 listing 段落；无可发现 skill 时返回空串。

        只列 discoverable=True 的 skill——元/基础设施 skill（discoverable=false）不进常驻 listing，
        但仍可被 Skill 工具按名直调。
        """
        skills = [
            s for s in self._loader.list()
            if s.discoverable and not s.disable_model_invocation
        ]
        if not skills:
            return ""
        lines: list[str] = [_SECTION_HEADER, "", _SECTION_RULE, ""]
        for s in skills:
            # 与 Claude Code 一致：listing 给 name + (description[ - when_to_use]) 首行、硬截到上限。
            # when_to_use 是 CC 的"何时用我"字段，拼进描述帮模型判断（对 CC getCommandDescription）；
            # 第三方 SKILL.md 数百字的长触发文不该整段灌进常驻 prompt。keywords/正文不进这里。
            desc = s.description.strip()
            if s.when_to_use:
                desc = f"{desc} - {s.when_to_use}" if desc else s.when_to_use
            desc_first = desc.splitlines()[0] if desc else ""
            if len(desc_first) > _MAX_LISTING_DESC_CHARS:
                desc_first = desc_first[: _MAX_LISTING_DESC_CHARS - 1].rstrip() + "…"
            lines.append(f"- **{s.name}** — {desc_first}")
        _logger.debug("skills listing 已构造", count=len(skills))
        return "\n".join(lines)
