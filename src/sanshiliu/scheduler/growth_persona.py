"""成长人格覆盖层（PR2）：版本化写 data/growth/persona/chapter-N/ + 给 PersonaLoader 的激活解析器。

为什么单独成一个纯模块（不塞进 growth_runner）：人格覆盖是"写哪、读哪、怎么连续"的
确定性文件操作，必须可单测、不依赖 LLM / engine。growth_runner 负责"跑一章拿结构化输出"，
本模块负责"把演化后的人格落成版本化目录 + 让 loader 解析到激活章"。

核心不变量（与 research/02 + prd R6 对齐）：
- **base persona/core/*.md 全程零写**：演化只写 data/growth/persona/chapter-N/。
- **每个 chapter 目录至少一份非空 md**：否则 PersonaLoader 触发 ConfigError；靠"从上一章
  目录拷贝起步、只覆盖 LLM 这章演化的段落"保证连续 + 非空（缺的段落自然承接前章）。
- **chapter-0 = 5 岁起点 = 原三十六贱笑**：首章前把 base core 整目录快照到 chapter-0。
- **激活解析**：provider 读 growth-state.json 的 active_persona_chapter，返回对应 chapter 目录；
  无成长 / 激活 0 且无 chapter-0 覆盖 → 返回 None，loader 回落 base core（日常对话不变）。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from sanshiliu.foundation.logging import get_logger
from sanshiliu.identity.types import CORE_DIRNAME
from sanshiliu.scheduler.growth_state import load_growth_state

_logger = get_logger(__name__)

# 成长人格根目录名（挂在 data_dir 下）：data/growth/persona/chapter-N/
_GROWTH_PERSONA_SUBDIR = ("growth", "persona")

# 演化时可被 LLM 覆盖的核心段落 ↔ 文件名映射；键与 SKILL.md 的 persona 对象字段一一对应。
# 顺序无关紧要（loader 按字母序拼接），但用 dict 固定字段集合，挡掉 LLM 乱塞的键。
PERSONA_SECTION_FILES: dict[str, str] = {
    "identity": "identity.md",
    "personality": "personality.md",
    "beliefs": "beliefs.md",
    "style": "style.md",
    "fewshot_short": "fewshot_short.md",
}


def growth_persona_root(data_dir: Path) -> Path:
    """成长人格版本化根目录：<data_dir>/growth/persona/。"""
    root = data_dir
    for part in _GROWTH_PERSONA_SUBDIR:
        root = root / part
    return root


def chapter_persona_dir(data_dir: Path, chapter_no: int) -> Path:
    """第 N 章人格目录：<data_dir>/growth/persona/chapter-N/（chapter-0 = 起点快照）。"""
    return growth_persona_root(data_dir) / f"chapter-{chapter_no}"


def _copy_core_md(src_dir: Path, dst_dir: Path) -> int:
    """把 src_dir 下所有 *.md 拷到 dst_dir（覆盖同名），返回拷贝份数；src 不存在则 0。

    只拷 *.md（人格目录里不该有别的东西）；用 copy2 保留 mtime 语义但 dst 是新目录无所谓。
    """
    if not src_dir.is_dir():
        return 0
    dst_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for p in sorted(src_dir.glob("*.md")):
        if p.is_file():
            shutil.copy2(p, dst_dir / p.name)
            count += 1
    return count


def snapshot_base_core_to_chapter0(persona_dir: Path, data_dir: Path) -> Path:
    """首章前把 base persona/core/*.md 整目录快照成 chapter-0（5 岁起点 = 原三十六贱笑）。

    幂等：chapter-0 已存在（含至少一份 md）则跳过，避免重复覆盖把已定格的起点改掉。
    返回 chapter-0 目录路径。base core 只读不写。
    """
    ch0 = chapter_persona_dir(data_dir, 0)
    if ch0.is_dir() and any(p.is_file() for p in ch0.glob("*.md")):
        return ch0
    base_core = persona_dir / CORE_DIRNAME
    n = _copy_core_md(base_core, ch0)
    _logger.info("成长人格起点已快照 chapter-0", files=n, src=str(base_core), dst=str(ch0))
    return ch0


def write_chapter_persona(
    *,
    data_dir: Path,
    chapter_no: int,
    prev_chapter_no: int,
    persona_sections: dict[str, str],
) -> Path:
    """写第 N 章演化人格目录：先整盘拷上一章（连续 + 非空兜底），再覆盖 LLM 这章演化的段落。

    连续性 + 非空保证：从 prev_chapter_no 的人格目录拷贝起步，所以本章**没演化的段落自动
    承接前一章**；persona_sections 里给了的段落才覆写。即使 LLM 这章一个段落都没给，
    本章目录也等于上一章（仍非空），日常对话仍有完整人格。

    参数:
        prev_chapter_no: 起步基线章（通常 chapter_no-1；首章是 0 = 起点快照）。
        persona_sections: 已过滤的 {section_key: markdown_text}，键限于 PERSONA_SECTION_FILES。
    返回本章人格目录路径。base core 不写。
    """
    dst = chapter_persona_dir(data_dir, chapter_no)
    prev = chapter_persona_dir(data_dir, prev_chapter_no)
    # 1) 整盘拷上一章作基线（承接前文、保证非空）
    copied = _copy_core_md(prev, dst)
    # 2) 覆盖本章 LLM 演化的段落
    dst.mkdir(parents=True, exist_ok=True)
    overwritten: list[str] = []
    for key, text in persona_sections.items():
        filename = PERSONA_SECTION_FILES.get(key)
        if filename is None:
            continue  # 防御：忽略 schema 外的键
        body = text.strip()
        if not body:
            continue  # 空段落不写，留前章承接（避免把人格段落清空触发 ConfigError）
        (dst / filename).write_text(body + "\n", encoding="utf-8")
        overwritten.append(filename)
    _logger.info(
        "成长人格本章已写入",
        chapter=chapter_no,
        carried_from=prev_chapter_no,
        carried_files=copied,
        overwritten=overwritten,
        dir=str(dst),
    )
    return dst


def filter_persona_sections(raw: object) -> dict[str, str]:
    """从结构化输出的 persona 对象里抽出合法段落：键限于 PERSONA_SECTION_FILES、值为非空串。

    缺失 / 非 dict / 字段不全都不报错——返回能用的子集（其余靠 write 时承接前章）。
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key in PERSONA_SECTION_FILES:
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val
    return out


def make_active_core_provider(
    growth_state_path: Path, data_dir: Path
) -> ActiveCoreProvider:
    """造一个给 PersonaLoader 的激活 core 解析器：读 state 的 active_persona_chapter → chapter 目录。

    返回 None 的情形（loader 会回落 base core）：
    - state 文件不存在（从未成长）；
    - active_persona_chapter <= 0（仍是起点）且对应 chapter-0 目录不存在/为空——即没有任何
      成长覆盖，等价于 base core，直接返回 None 省一层。
    loader 侧还有"目录必须存在且含 *.md"的守卫，这里只给路径意图。
    """
    return ActiveCoreProvider(growth_state_path, data_dir)


class ActiveCoreProvider:
    """可调用对象：每次调用读 growth-state.json 算当前激活人格目录（None = 用 base core）。

    用类而非闭包：mypy strict 下签名清晰，且便于在测试里直接构造/断言。
    """

    def __init__(self, growth_state_path: Path, data_dir: Path) -> None:
        self._state_path = growth_state_path
        self._data_dir = data_dir

    def __call__(self) -> Path | None:
        if not self._state_path.is_file():
            return None
        state = load_growth_state(self._state_path)
        active = state.active_persona_chapter
        if active <= 0:
            # active 0 = 起点；只有当 chapter-0 真有快照覆盖时才返回它，否则等价 base core
            ch0 = chapter_persona_dir(self._data_dir, 0)
            if ch0.is_dir() and any(p.is_file() for p in ch0.glob("*.md")):
                return ch0
            return None
        return chapter_persona_dir(self._data_dir, active)
