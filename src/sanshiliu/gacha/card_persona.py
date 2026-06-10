"""卡人格快照链：版本化写 <card>/persona/chapter-N/ + 出生底版快照（根目录参数化）。

平移自 scheduler/growth_persona（老链路冻结待退役），差别只有一处：根目录从全局
data/growth/persona/ 参数化为每张卡自己的 persona_root（= cards/<id>/persona/），
让多张卡的人格链互不串门。核心不变量原样保留：

- **base persona/core/*.md 全程零写**：演化只写卡目录下的 chapter-N/。
- **每个 chapter 目录至少一份非空 md**：从上一章目录整盘拷贝起步、只整段覆盖 LLM 这章
  演化的段落——缺的段落自然承接前章，核心永不为空。
- **协议/红线不被演化改写**：载体协议与安全红线住在 persona/core/_protocol.md，不在
  PERSONA_SECTION_FILES 五段之内，被 _copy_core_md 每章原样带走、永不被 LLM 覆盖。
- **chapter-0 = 出生底版 = 原三十六贱笑底色**：首章前把 base core 整目录快照成 chapter-0
  （卡是平行世界分身：人格底色继承本体，出身/际遇由命运种子决定）。

激活解析器（转生用 ActiveCardProvider）不在本模块——那是 PR3 的事，锻造期不触碰
PersonaLoader / 本体当前人格。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from sanshiliu.foundation.logging import get_logger
from sanshiliu.identity.types import CORE_DIRNAME

_logger = get_logger(__name__)

# 演化时可被 LLM 覆盖的核心段落 ↔ 文件名映射；键与 skills/gacha/SKILL.md 的 persona 字段一一对应。
PERSONA_SECTION_FILES: dict[str, str] = {
    "identity": "identity.md",
    "personality": "personality.md",
    "beliefs": "beliefs.md",
    "style": "style.md",
    "fewshot_short": "fewshot_short.md",
}


def chapter_persona_dir(persona_root: Path, chapter_no: int) -> Path:
    """第 N 章人格目录：<persona_root>/chapter-N/（chapter-0 = 出生底版快照）。"""
    return persona_root / f"chapter-{chapter_no}"


def _copy_core_md(src_dir: Path, dst_dir: Path) -> int:
    """把 src_dir 下所有 *.md 拷到 dst_dir（覆盖同名），返回拷贝份数；src 不存在则 0。"""
    if not src_dir.is_dir():
        return 0
    dst_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for p in sorted(src_dir.glob("*.md")):
        if p.is_file():
            shutil.copy2(p, dst_dir / p.name)
            count += 1
    return count


def snapshot_base_core_to_chapter0(persona_dir: Path, persona_root: Path) -> Path:
    """首章前把 base persona/core/*.md 整目录快照成本卡 chapter-0（出生底版）。

    幂等：chapter-0 已存在（含至少一份 md）则跳过。base core 只读不写。
    """
    ch0 = chapter_persona_dir(persona_root, 0)
    if ch0.is_dir() and any(p.is_file() for p in ch0.glob("*.md")):
        return ch0
    base_core = persona_dir / CORE_DIRNAME
    n = _copy_core_md(base_core, ch0)
    _logger.info("卡人格出生底版已快照 chapter-0", files=n, src=str(base_core), dst=str(ch0))
    return ch0


def write_chapter_persona(
    *,
    persona_root: Path,
    chapter_no: int,
    prev_chapter_no: int,
    persona_sections: dict[str, str],
) -> Path:
    """写第 N 章演化人格目录：先整盘拷上一章（连续 + 非空兜底），再整段覆盖本章演化段落。

    连续性 + 非空保证：从 prev_chapter_no 的目录拷贝起步，本章**没演化的段落自动承接前章**；
    persona_sections 里给了的段落才整段覆写。即使 LLM 这章一个段落都没给，本章目录也等于
    上一章（仍非空）。协议/红线在 _protocol.md（不在五段之内）随拷贝原样带走、永不被覆盖。
    """
    dst = chapter_persona_dir(persona_root, chapter_no)
    prev = chapter_persona_dir(persona_root, prev_chapter_no)
    copied = _copy_core_md(prev, dst)
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
        "卡人格本章已写入",
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
