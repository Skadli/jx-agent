"""老成长线 → 创始卡 origin 的幂等迁移（设计 §12）。

把 data/growth-state.json（已锻 N 章）、data/growth/persona/chapter-*、memdir 里的
reference_growth-chapter-N_*.md 传记，**只复制不删除**地迁成 cards/origin/——本体
三十六贱笑从此以创始卡身份住进卡池（设计决策 #4「本体即卡」），可续锻到 60 岁。
旧文件全部原地保留（可追溯；老 growth 链路 PR3 退出 serve 主链路前仍在读它们）。

幂等：cards/origin/card.json 已存在 → 整体跳过（绝不覆盖已迁移/已续锻的创始卡）。
无旧数据（从未开过 growth）→ 建一张空白创始卡（0 章、起点年龄），保证 origin 恒存在
（它是转生回滚的锚点，PR3 消费）。

cadence 以旧状态文件为真相源（有人跑过 1 年/章的老节奏，不能按新默认重排年龄段）；
本次升级项只有 end_age（30 → 60）：end_chapter 按旧 start_age / years_per_chapter 重推。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from sanshiliu.foundation.frontmatter import parse as _parse_markup
from sanshiliu.foundation.logging import get_logger
from sanshiliu.gacha.card_state import (
    ORIGIN_CARD_ID,
    CardSeed,
    ChapterRecord,
    biography_dir,
    card_json_path,
    cards_root,
    coerce_chapters,
    new_card_state,
    persona_root,
    save_card_state,
)
from sanshiliu.identity.types import CORE_DIRNAME

_logger = get_logger(__name__)

_ORIGIN_TITLE = "三十六贱笑·本源"

_PROTOCOL_FILENAME = "_protocol.md"


def migrate_origin_card(
    *,
    gacha_root: Path,
    growth_state_path: Path,
    growth_persona_dir: Path,
    memdir_dir: Path,
    start_age: int = 5,
    years_per_chapter: int = 5,
    end_age: int = 60,
    birth_year: int = 1992,
) -> bool:
    """执行一次创始卡迁移；True=本次迁移了（含"无旧数据建空白创始卡"），False=已存在跳过。"""
    if card_json_path(gacha_root, ORIGIN_CARD_ID).is_file():
        _logger.info("创始卡已存在，迁移跳过", card_id=ORIGIN_CARD_ID)
        return False

    raw = _read_old_state(growth_state_path)
    chapters = coerce_chapters(raw.get("chapters")) if raw is not None else []
    old_start = _int_or(raw, "start_age", start_age)
    old_ypc = _int_or(raw, "years_per_chapter", years_per_chapter)

    seed = CardSeed(
        genre="legacy",
        genre_label="写实喜剧线（创始）",
        origin="汕头市金平区双职工（财务+会计）家庭独生子",
        talents=["天生嘴贱（损人不带脏字）", "讲故事的天生节奏感"],
        trigger="",
        creativity=1.0,
        custom_prompt="",
        birth_year=birth_year,
    )
    state = new_card_state(
        card_id=ORIGIN_CARD_ID,
        seed=seed,
        start_age=old_start,
        years_per_chapter=old_ypc,
        end_age=end_age,
        title=_ORIGIN_TITLE,
    )
    # 章数组是真相：current/age/active 一律按它收敛（旧文件字段只作 active 的参考上界）
    state.chapters = chapters
    state.current_chapter = len(chapters)
    state.age = old_start + state.current_chapter * old_ypc
    old_active = _int_or(raw, "active_persona_chapter", state.current_chapter)
    state.active_persona_chapter = min(max(old_active, 0), state.current_chapter)
    state.status = "paused" if state.can_advance() else "complete"
    save_card_state(gacha_root, state)

    persona_dirs = _copy_persona_chapters(
        growth_persona_dir, persona_root(gacha_root, ORIGIN_CARD_ID)
    )
    biographies = _copy_biographies(memdir_dir, biography_dir(gacha_root, ORIGIN_CARD_ID), chapters)
    _logger.info(
        "创始卡迁移完成（旧文件原地保留）",
        card_id=ORIGIN_CARD_ID,
        chapters=state.current_chapter,
        age=state.age,
        end_age=state.end_age,
        persona_dirs=persona_dirs,
        biographies=biographies,
        had_old_state=raw is not None,
    )
    return True


def backfill_protocol_md(gacha_root: Path, persona_dir: Path) -> int:
    """给已有卡的人格章目录补拷缺失的 _protocol.md（载体协议/红线永驻层）；返回补拷份数。

    背景：创始卡迁移自老成长数据，那批章目录早于 _protocol.md 引入、缺协议层——被激活
    （转生/默认 origin）时 system prompt 会失去载体协议。幂等且克制：已有该文件的章不动、
    空目录不造文件（只补"已有其他 md 的真实章"）、base core 本身没有 _protocol.md 则整体
    no-op。serve / REPL 启动在迁移后调用一次；后续章从前一章整盘拷贝，自然带着它。
    """
    src = persona_dir / CORE_DIRNAME / _PROTOCOL_FILENAME
    if not src.is_file():
        return 0
    root = cards_root(gacha_root)
    if not root.is_dir():
        return 0
    count = 0
    for card_dir_ in root.iterdir():
        if not card_dir_.is_dir():
            continue
        proot = card_dir_ / "persona"
        if not proot.is_dir():
            continue
        for ch_dir in proot.iterdir():
            if not ch_dir.is_dir() or not ch_dir.name.startswith("chapter-"):
                continue
            dst = ch_dir / _PROTOCOL_FILENAME
            if dst.is_file():
                continue
            if not any(p.is_file() for p in ch_dir.glob("*.md")):
                continue  # 空目录不是真实章，别凭空造
            try:
                shutil.copy2(src, dst)
                count += 1
            except OSError as exc:
                _logger.warning(
                    "_protocol.md 补拷失败（跳过该章）",
                    card=card_dir_.name,
                    chapter_dir=ch_dir.name,
                    error=str(exc),
                )
    if count:
        _logger.info("已为旧人格章补拷 _protocol.md 永驻层", copied=count)
    return count


def _read_old_state(path: Path) -> dict[str, Any] | None:
    """读老 growth-state.json；不存在/坏 JSON/非 dict → None（按"从未成长"处理）。"""
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("老 growth-state.json 解析失败，按无旧数据迁移", error=str(exc))
        return None
    return raw if isinstance(raw, dict) else None


def _int_or(raw: dict[str, Any] | None, key: str, default: int) -> int:
    if raw is None:
        return default
    v = raw.get(key)
    return v if isinstance(v, int) and not isinstance(v, bool) else default


def _copy_persona_chapters(src_dir: Path, dst_root: Path) -> int:
    """把老 data/growth/persona/chapter-* 整目录复制到卡的 persona/ 下；返回复制的章目录数。"""
    if not src_dir.is_dir():
        return 0
    count = 0
    for child in sorted(src_dir.iterdir()):
        if not child.is_dir() or not child.name.startswith("chapter-"):
            continue
        try:
            shutil.copytree(child, dst_root / child.name, dirs_exist_ok=True)
            count += 1
        except OSError as exc:
            _logger.warning(
                "创始卡人格章复制失败（跳过该章）", chapter_dir=child.name, error=str(exc)
            )
    return count


def _copy_biographies(memdir_dir: Path, bio_dir: Path, chapters: list[ChapterRecord]) -> int:
    """把 memdir 里各章传记（最新一份）剥掉 frontmatter 复制成卡目录 chapter-N.md；返回份数。

    memdir 原文件保留——它们已是本体记忆史的一部分，不抹；新链路只读卡目录这份。
    """
    if not memdir_dir.is_dir() or not chapters:
        return 0
    bio_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for n in range(1, len(chapters) + 1):
        # 前缀严格到 chapter-N_，避免 chapter-1 误匹配 chapter-10（下划线分隔时间戳）
        matches = sorted(memdir_dir.glob(f"reference_growth-chapter-{n}_*.md"))
        if not matches:
            continue
        try:
            text = matches[-1].read_text(encoding="utf-8")
        except OSError:
            continue
        body = _strip_frontmatter(text)
        age_range = chapters[n - 1].age_range
        content = f"# 第 {n} 章 · {age_range} 岁\n\n{body.strip()}\n"
        try:
            (bio_dir / f"chapter-{n}.md").write_text(content, encoding="utf-8")
            count += 1
        except OSError as exc:
            _logger.warning("创始卡传记复制失败（跳过该章）", chapter=n, error=str(exc))
    return count


def _strip_frontmatter(text: str) -> str:
    try:
        return _parse_markup(text).body
    except ValueError:
        return text
