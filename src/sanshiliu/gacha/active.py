"""全渠道激活指针（转生）：data/gacha/active.json 读写 + 给 PersonaLoader 的激活解析器。

转生 = 把某张卡设为所有渠道（web/REPL/微信）的当前人格（设计决策 #4「本体即卡」+ #8）：

- active.json 形状：`{"card_id": "...", "chapter": null|int}`。chapter 为 null = **follow**
  模式——解析为该卡 card.json 的 active_persona_chapter（卡续锻长大、真身跟着长，与老
  成长链"推进即激活最新章"一脉相承）；给定整数 = 钉住某章（预留章级回滚，v1 API 只写 null）。
- 解析链（ActiveCardProvider.__call__，**每次调用都重读文件**——REPL/serve 跨进程也能
  跟上转生，PersonaWatcher 的 mtime 轮询负责热失效）：
  active.json 指向的卡 → 人格章目录；文件缺失 / 卡损坏 / 目录为空 → 创始卡 origin 同法
  解析 → 仍不可用 → None（PersonaLoader 回落 base core）。
- 只动指针、不删数据：转生 / 回滚都只改这一个 JSON；二次确认在 dashboard 层（PR4）。

接替 scheduler/growth_persona.make_active_core_provider（老链路冻结待删）；创始卡迁移
保证了换 provider 后的人格连续性（active.json 缺省 → origin 的 active_persona_chapter
= 迁移自老 growth-state 的同一章）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sanshiliu.foundation.logging import get_logger
from sanshiliu.gacha.card_persona import chapter_dir_has_md, chapter_persona_dir
from sanshiliu.gacha.card_state import (
    ORIGIN_CARD_ID,
    CardState,
    card_json_path,
    is_valid_card_id,
    load_card_state,
    persona_root,
)

_logger = get_logger(__name__)

ACTIVE_JSON_FILENAME = "active.json"


@dataclass
class ActivePointer:
    """激活指针：card_id 必填；chapter None = follow 该卡最新激活章，int = 钉住某章。"""

    card_id: str
    chapter: int | None = None


def active_json_path(gacha_root: Path) -> Path:
    return gacha_root / ACTIVE_JSON_FILENAME


def load_active_pointer(gacha_root: Path) -> ActivePointer | None:
    """读激活指针；文件缺失 / 坏 JSON / card_id 非法 → None（解析链回落创始卡）。"""
    path = active_json_path(gacha_root)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("active.json 解析失败（回落创始卡）", path=str(path), error=str(exc))
        return None
    if not isinstance(raw, dict):
        return None
    card_id = raw.get("card_id")
    if not isinstance(card_id, str) or not is_valid_card_id(card_id):
        return None
    chapter = raw.get("chapter")
    if not isinstance(chapter, int) or isinstance(chapter, bool) or chapter < 0:
        chapter = None
    return ActivePointer(card_id=card_id, chapter=chapter)


def save_active_pointer(gacha_root: Path, pointer: ActivePointer) -> None:
    """原子写激活指针（先 .tmp 再 rename）；半写文件会让下次解析回落创始卡而非读脏。"""
    path = active_json_path(gacha_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(
            {"card_id": pointer.card_id, "chapter": pointer.chapter},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    tmp.replace(path)
    _logger.info("激活指针已写入", card_id=pointer.card_id, chapter=pointer.chapter)


def resolve_persona_dir_for_card(
    gacha_root: Path, card_id: str, chapter: int | None = None
) -> tuple[Path, int] | None:
    """卡 → (人格章目录, 章号)；卡不可读 / 目录无 *.md → None。

    chapter None = follow 该卡 active_persona_chapter；给定但越界（> current_chapter）
    也回落 follow——钉住的章被 delete 掉时宁可跟最新、不要指向空目录。
    """
    state = load_card_state(gacha_root, card_id)
    if state is None:
        return None
    return _resolve_from_state(gacha_root, state, chapter)


def _resolve_from_state(
    gacha_root: Path, state: CardState, chapter: int | None
) -> tuple[Path, int] | None:
    n = (
        chapter
        if chapter is not None and 0 <= chapter <= state.current_chapter
        else state.active_persona_chapter
    )
    ch_dir = chapter_persona_dir(persona_root(gacha_root, state.card_id), n)
    if chapter_dir_has_md(ch_dir):
        return ch_dir, n
    return None


def resolve_active(
    gacha_root: Path,
) -> tuple[str, ActivePointer | None, CardState | None, tuple[Path, int] | None]:
    """解析链唯一权威：provider / describe_active / GET /api/gacha/active 都走这里。

    返回 (source, 指针, 解析成功的卡 state, (人格目录, 章号))；
    source：pointer=按指针解析成功；default_origin=指针缺失/不可用、落在创始卡；
    base_core=连创始卡都不可用（从未迁移/全空），日常对话用的是 base persona/core。
    """
    pointer = load_active_pointer(gacha_root)
    if pointer is not None:
        state = load_card_state(gacha_root, pointer.card_id)
        if state is not None:
            resolved = _resolve_from_state(gacha_root, state, pointer.chapter)
            if resolved is not None:
                return "pointer", pointer, state, resolved
    # 指针缺失/不可用 → 创始卡（迁移保证它与老成长链同章，人格连续）；origin 也没有 → base core
    state = load_card_state(gacha_root, ORIGIN_CARD_ID)
    if state is not None:
        resolved = _resolve_from_state(gacha_root, state, None)
        if resolved is not None:
            return "default_origin", pointer, state, resolved
    return "base_core", pointer, None, None


def _stat_sig(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


class ActiveCardProvider:
    """可调用对象：按 active.json → origin 的链解析当前激活人格目录（None=base core）。

    供 PersonaLoader 的 active_core_provider 钩子用；高频调用（每轮 + watcher 5s 轮询），
    解析失败一律静默回落、不打日志（写路径的 save/转生 API 才记日志）。

    带 stat 失效缓存：贵的是整本 card.json（含全部章叙事，11 章 ≈ 70KB）的 json.loads——
    不缓存等于 watcher 每天上万次全量解析只为读一个章号。active.json 只有几十字节，每次
    重读无所谓；两份状态文件都是 tmp+replace 原子写，(mtime_ns, size) 是可靠失效信号，
    跨进程转生/锻造推进都会改键。绕过 card.json 手工改章目录不在失效范围（重启收敛）。
    """

    def __init__(self, gacha_root: Path) -> None:
        self._gacha_root = gacha_root
        self._cache: tuple[tuple[object, ...], Path | None] | None = None

    def __call__(self) -> Path | None:
        key = self._cache_key()
        if key is not None and self._cache is not None and self._cache[0] == key:
            return self._cache[1]
        _, _, _, resolved = resolve_active(self._gacha_root)
        result = resolved[0] if resolved is not None else None
        if key is not None:
            self._cache = (key, result)
        return result

    def _cache_key(self) -> tuple[object, ...] | None:
        """失效键 = 指针内容 + 指针卡与创始卡两份 card.json 的 stat（任一变化都重解析）。

        两张卡都进键：指针指向的坏卡补锻成可用后，解析结果会从 origin 回切到指针卡，
        只盯 origin 的 stat 会漏掉这次切换。组键失败 → None（放弃缓存，直接全量解析）。
        """
        try:
            pointer = load_active_pointer(self._gacha_root)
            parts: list[object] = []
            if pointer is not None:
                parts.extend((pointer.card_id, pointer.chapter))
                parts.append(_stat_sig(card_json_path(self._gacha_root, pointer.card_id)))
            else:
                parts.append(None)
            parts.append(_stat_sig(card_json_path(self._gacha_root, ORIGIN_CARD_ID)))
            return tuple(parts)
        except Exception:
            return None


def make_active_card_provider(gacha_root: Path) -> ActiveCardProvider:
    return ActiveCardProvider(gacha_root)


def describe_active(gacha_root: Path) -> dict[str, Any]:
    """当前激活态的可读快照（GET /api/gacha/active 用）；解析口径 = resolve_active。"""
    source, pointer, state, resolved = resolve_active(gacha_root)
    return {
        "source": source,
        "card_id": state.card_id if state is not None else None,
        "pinned_chapter": pointer.chapter
        if (source == "pointer" and pointer is not None)
        else None,
        "resolved_chapter": resolved[1] if resolved is not None else None,
    }
