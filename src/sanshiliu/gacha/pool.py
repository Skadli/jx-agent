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
    cards_root,
    is_valid_card_id,
    load_card_state,
)

_logger = get_logger(__name__)


def card_summary(state: CardState) -> dict[str, Any]:
    """卡面摘要（列表用）：不含各章正文，几百字节一张。"""
    return {
        "card_id": state.card_id,
        "title": state.title,
        "status": state.status,
        "genre": state.seed.genre,
        "genre_label": state.seed.genre_label,
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


def list_cards(gacha_root: Path) -> list[dict[str, Any]]:
    """扫卡目录出全量卡面摘要；坏 card.json 跳过（load 内已 warning）。最新在前。"""
    root = cards_root(gacha_root)
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        state = load_card_state(gacha_root, child.name)
        if state is None:
            continue
        out.append(card_summary(state))
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


def draws_today(gacha_root: Path, *, now: float | None = None) -> int:
    """今天（本地日期）抽出的卡数——按 card.json 的 created_at 计，创始卡不算。

    每日抽卡上限（config gacha_daily_draw_limit）用它判额度；删掉的卡不再计入
    （以现存目录为准——删卡重抽是主人自己的选择，护栏挡的是无意识连抽烧钱）。
    """
    ts = now if now is not None else time.time()
    today = time.localtime(ts)[:3]
    root = cards_root(gacha_root)
    if not root.is_dir():
        return 0
    count = 0
    for child in root.iterdir():
        if not child.is_dir() or child.name == ORIGIN_CARD_ID:
            continue
        state = load_card_state(gacha_root, child.name)
        if state is None:
            continue
        if time.localtime(state.created_at)[:3] == today:
            count += 1
    return count
