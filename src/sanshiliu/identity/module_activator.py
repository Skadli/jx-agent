"""persona module 激活器；引擎层关键词预判 + 生成常驻 listing 段。

与 SkillActivator 形态相似（list_for_prompt 同名），但语义不同：
- SkillActivator 暴露的是 LLM 主动调的工具列表
- PersonaModuleActivator 暴露的是「我可以多了解哪些方面」的 module 目录，
  引擎层会基于 user_text 关键词命中后**直接注入正文**到 system prompt。
"""

from __future__ import annotations

from sanshiliu.foundation.logging import get_logger
from sanshiliu.identity.module_loader import PersonaModuleLoader
from sanshiliu.identity.module_types import PersonaModule

_logger = get_logger(__name__)

# 注入 system prompt 的章节标题；让 LLM 知道这块是 modules 目录
_LISTING_HEADER = "# 可加载的人设模块（persona modules）"

# 提示 LLM 这些模块的存在 + 调用方式
_LISTING_HINT = (
    "下列模块描述了我的额外知识（作品库 / 创作方法论 / 长样本）。"
    "引擎会按用户消息关键词自动注入相关模块；若引擎漏判而你需要某个模块的正文，"
    "调 `LoadPersonaModule` 工具并传 `name` 即可。一轮对话最多只用 1 个模块的正文。"
)

# 命中正文段的章节标题模板；engine 注入到 active_module_text 时套用
_BODY_HEADER_TPL = "# 当前激活的人设模块：{name}\n\n"


class PersonaModuleActivator:
    """组合 loader + 简单关键词匹配；pick 返回 0 或 1 个 module。"""

    def __init__(self, loader: PersonaModuleLoader) -> None:
        self._loader = loader

    def list_all(self) -> list[PersonaModule]:
        return self._loader.list()

    def lookup(self, name_or_id: str) -> PersonaModule | None:
        return self._loader.lookup(name_or_id)

    def list_for_prompt(self) -> str:
        """生成常驻 listing 段；无 module 时返回空串（engine 会跳过该段）。"""
        mods = self._loader.list()
        if not mods:
            return ""
        lines: list[str] = [_LISTING_HEADER, "", _LISTING_HINT, ""]
        for m in mods:
            desc_first = m.description.strip().splitlines()[0] if m.description.strip() else ""
            lines.append(f"- **{m.name}** — {desc_first}")
        return "\n".join(lines)

    def pick(self, user_text: str) -> PersonaModule | None:
        """关键词命中算法：substring 匹配，命中数最多的赢；同分按字母序 tie-break。

        小写比较；中文不存在大小写问题，但用户可能用英文场景词（vlog/AI）。
        命中 0 个返回 None。
        """
        if not user_text:
            return None
        text_lc = user_text.lower()
        scored: list[tuple[int, str, PersonaModule]] = []
        for m in self._loader.list():
            score = sum(1 for kw in m.trigger_keywords if kw and kw.lower() in text_lc)
            if score > 0:
                scored.append((score, m.id, m))
        if not scored:
            return None
        # 命中数倒序 + id 正序 → 稳定 tie-break
        scored.sort(key=lambda t: (-t[0], t[1]))
        chosen = scored[0][2]
        _logger.debug(
            "persona module 命中",
            module=chosen.id, score=scored[0][0],
            total_candidates=len(scored),
        )
        return chosen

    def render_body(self, module: PersonaModule) -> str:
        """生成 module 正文的注入段（含 header）。给 engine 和工具复用。"""
        return _BODY_HEADER_TPL.format(name=module.name) + module.body.strip()
