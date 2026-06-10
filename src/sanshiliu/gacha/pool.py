"""卡册：扫卡目录出列表/摘要、删卡（创始卡保护）、每日抽卡计数。

无独立索引文件——cards/*/card.json 是唯一真相源（量级百内直接扫，避免双真相源漂移）。
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from sanshiliu.foundation.logging import get_logger
from sanshiliu.gacha.card_state import (
    ORIGIN_CARD_ID,
    CardState,
    card_dir,
    card_json_path,
    cards_root,
    is_valid_card_id,
    load_card_state,
    save_card_state,
)
from sanshiliu.gacha.seeds import find_genre

_logger = get_logger(__name__)

# 卡面图标兜底：创始卡 legacy 线给 🎬，其余未知类型给通用卡背
_LEGACY_GENRE_ICON = "🎬"
_FALLBACK_GENRE_ICON = "🎴"

# 卡面摘要缓存：card_id → ((mtime_ns, size), summary)。card.json 是 tmp+replace 原子写，
# stat 是可靠失效信号；dashboard 每 10s 轮询列表，不缓存等于每天数万次全量解析整本
# card.json（含全部章叙事，11 章 ≈ 70KB）只为出几百字节卡面。
_summary_cache: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}


def _genre_icon(genre: str) -> str:
    spec = find_genre(genre)
    if spec is not None:
        return spec.icon
    return _LEGACY_GENRE_ICON if genre == "legacy" else _FALLBACK_GENRE_ICON


def card_summary(state: CardState) -> dict[str, Any]:
    """卡面摘要（列表用）：不含各章正文，几百字节一张。"""
    return {
        "card_id": state.card_id,
        "title": state.title,
        "status": state.status,
        "genre": state.seed.genre,
        "genre_label": state.seed.genre_label,
        "genre_icon": _genre_icon(state.seed.genre),
        "origin": state.seed.origin,
        "trigger": state.seed.trigger,
        "talents": list(state.seed.talents),
        "creativity": state.seed.creativity,
        "age": state.age,
        "current_chapter": state.current_chapter,
        "end_chapter": state.end_chapter,
        "end_age": state.end_age,
        "grade": state.rarity.grade,
        "score": state.rarity.score,
        "skill_count": state.installed_skill_count(),
        "created_at": state.created_at,
        "is_origin": state.card_id == ORIGIN_CARD_ID,
    }


def _cached_summary(gacha_root: Path, card_id: str) -> dict[str, Any] | None:
    """带 stat 失效缓存的单卡摘要；文件没动就不重新解析。返回浅拷贝（防调用方改缓存）。"""
    sig_st = None
    try:
        st = card_json_path(gacha_root, card_id).stat()
        sig_st = (st.st_mtime_ns, st.st_size)
    except OSError:
        _summary_cache.pop(card_id, None)
        return None
    cached = _summary_cache.get(card_id)
    if cached is not None and cached[0] == sig_st:
        return dict(cached[1])
    state = load_card_state(gacha_root, card_id)
    if state is None:
        return None
    summary = card_summary(state)
    _summary_cache[card_id] = (sig_st, summary)
    return dict(summary)


def list_cards(gacha_root: Path) -> list[dict[str, Any]]:
    """扫卡目录出全量卡面摘要；坏 card.json 跳过（load 内已 warning）。最新在前。"""
    root = cards_root(gacha_root)
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        summary = _cached_summary(gacha_root, child.name)
        if summary is not None:
            out.append(summary)
    out.sort(key=lambda c: float(c["created_at"]), reverse=True)
    return out


def delete_card(gacha_root: Path, card_id: str) -> tuple[bool, str]:
    """删一张卡（整目录 rmtree）；返回 (是否删除, 拒绝原因)。

    创始卡 origin 永存不可删（设计决策 #8 的回滚锚点）；card_id 非法/不存在也拒绝。
    """
    if not is_valid_card_id(card_id):
        return False, "card_id 非法"
    if card_id == ORIGIN_CARD_ID:
        return False, "创始卡不可删除"
    target = card_dir(gacha_root, card_id)
    if not target.is_dir():
        return False, "卡不存在"
    try:
        shutil.rmtree(target)
    except OSError as exc:
        _logger.error("删卡失败", card_id=card_id, error=str(exc))
        return False, f"删除失败：{exc}"
    _logger.info("卡已删除", card_id=card_id)
    return True, ""


def draws_today_from_summaries(cards: list[dict[str, Any]], *, now: float | None = None) -> int:
    """从已构建的卡面摘要算今日抽卡数（创始卡不算）；列表端点已有摘要时别再重扫一遍目录。"""
    ts = now if now is not None else time.time()
    today = time.localtime(ts)[:3]
    return sum(
        1
        for c in cards
        if not c.get("is_origin") and time.localtime(float(c["created_at"]))[:3] == today
    )


def draws_today(gacha_root: Path, *, now: float | None = None) -> int:
    """今天（本地日期）抽出的卡数——按 card.json 的 created_at 计，创始卡不算。

    每日抽卡上限（config gacha_daily_draw_limit）用它判额度；删掉的卡不再计入
    （以现存目录为准——删卡重抽是主人自己的选择，护栏挡的是无意识连抽烧钱）。
    """
    return draws_today_from_summaries(list_cards(gacha_root), now=now)


def reset_stale_forging(gacha_root: Path) -> list[str]:
    """启动清扫：把所有 status=forging 的卡归位 paused，返回被清的 card_id。

    锻造协程随进程死（ForgeGate 是进程内锁），serve 刚启动时不可能有真在锻的卡——
    上次进程被杀/崩溃留下的 forging 是僵尸状态，会让 dashboard 永远显示「锻造中」并
    锁死续锻/转生/删除按钮。**只该由 serve 启动调用**（REPL 不锻造，且 serve 可能正在
    另一进程里真锻着，REPL 清扫会误伤）。
    """
    fixed: list[str] = []
    root = cards_root(gacha_root)
    if not root.is_dir():
        return fixed
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        state = load_card_state(gacha_root, child.name)
        if state is None or state.status != "forging":
            continue
        state.status = "paused"
        save_card_state(gacha_root, state)
        fixed.append(state.card_id)
    if fixed:
        _logger.warning("启动清扫：中断遗留的锻造中状态已归位 paused（可续锻）", cards=fixed)
    return fixed
