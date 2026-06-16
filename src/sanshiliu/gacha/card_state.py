"""人生卡状态机；data/gacha/cards/<card_id>/card.json 的 load/save/advance + 目录布局。

为什么单独成一个纯模块（与 scheduler/growth_state 同理）：锻造推进是有限状态机
（默认 5 岁→60 岁、5 年/章共 11 章），逻辑必须可单测、不依赖 LLM / engine / 网络。
与 growth_state 的差别只有"单例 → 多实例"：状态文件从全局 growth-state.json 变成每张卡
自己的 card.json，并多出卡元信息（seed 命运种子 / rarity 评级 / status 生命周期 / title 卡名）。
推进字段语义与老状态机对齐（current_chapter / age / active_persona_chapter / chapters 同义），
创始卡迁移（migrate.py）因此可平移旧数据。本模块平移自老链路、**不 import 它**
（老 scheduler/growth_* 冻结待退役，新链路不能依赖将删除的代码）。

status 生命周期（forging | paused | complete | error）：
    forging  锻造循环进行中（draw / 续锻期间由 ForgeRunner 置入）
    paused   未到定格年龄且当前没有锻造在跑（中断恢复 / 限章冒烟 / 创始卡待续锻）
    complete 已锻满 end_chapter 章（60 岁定格 = 完整人格；评级 best-effort 附着）
    error    某章 phase-1 不可恢复失败（已成立的章保留，可续锻重试）

卡目录布局（gacha_root = <data_dir>/gacha）：
    <gacha_root>/cards/<card_id>/card.json              状态文件（本模块管）
    <gacha_root>/cards/<card_id>/biography/chapter-N.md 各章传记（forge_runner 写，不进 memdir）
    <gacha_root>/cards/<card_id>/persona/chapter-0..N/  人格快照链（card_persona 写）
"""

from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from sanshiliu.foundation.logging import get_logger

_logger = get_logger(__name__)

CARDS_DIRNAME = "cards"
CARD_JSON_FILENAME = "card.json"
BIOGRAPHY_DIRNAME = "biography"
PERSONA_DIRNAME = "persona"

# 创始卡固定 id：本体三十六贱笑的老成长线迁移而来；永存、不可删，是转生回滚的锚点（PR3 消费）。
ORIGIN_CARD_ID = "origin"

# 默认值——与 config 的 gacha_* 默认一致（5 岁起、5 年/章、60 岁止 → 共 11 章）
_DEFAULT_START_AGE = 5
_DEFAULT_YEARS_PER_CHAPTER = 5
_DEFAULT_END_AGE = 60

CardStatus = Literal["forging", "paused", "complete", "error"]
_VALID_STATUS: tuple[CardStatus, ...] = ("forging", "paused", "complete", "error")

# card_id 合法形状：目录名直接来自它，锁死字符集挡路径注入（API 层 PR2 也复用这条校验）
_CARD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


@dataclass
class ChapterRecord:
    """一章锻造的产物快照；append 进 CardState.chapters（与老 growth_state 同形）。"""

    age_range: str
    summary: str
    report: str = ""
    installed_skills: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


@dataclass
class CardSeed:
    """命运种子。抽卡瞬间定下的只有"方向"：genre / creativity / divergence / custom_prompt；
    origin / talents / trigger **留空**，由第1章大模型现写后回填
    （forge_runner._capture_seed_background），之后每章 prompt 常驻注入、保证背景全程延续。"""

    genre: str = "random"
    genre_label: str = "随机"
    origin: str = ""
    talents: list[str] = field(default_factory=list)
    trigger: str = ""
    creativity: float = 1.0
    custom_prompt: str = ""
    # 写实锚：年龄 0 = 该公历年；非写实剧情以故事内在时间线为准、公历仅作参考（同老协议）
    birth_year: int = 1992
    # 发散种子：抽卡时随机撒下，喂给第1章 prompt 逼开头不收敛成套路（非题库、不约束内容，只给熵）
    divergence: int = 0


@dataclass
class CardRarity:
    """跑完定级的产物；grade 空串 = 未评级（评级 best-effort，失败不否定已锻成的卡）。"""

    grade: str = ""
    score: int = 0
    comment: str = ""


@dataclass
class CardState:
    """单张人生卡的完整状态；current_chapter 是已完成章数，满 end_chapter 定格。"""

    card_id: str
    title: str = ""
    status: CardStatus = "paused"
    seed: CardSeed = field(default_factory=CardSeed)
    rarity: CardRarity = field(default_factory=CardRarity)
    current_chapter: int = 0
    age: int = _DEFAULT_START_AGE
    active_persona_chapter: int = 0
    start_age: int = _DEFAULT_START_AGE
    years_per_chapter: int = _DEFAULT_YEARS_PER_CHAPTER
    # 总章数；由 (end_age - start_age) / years_per_chapter 推出（默认 (60-5)/5 = 11）
    end_chapter: int = (_DEFAULT_END_AGE - _DEFAULT_START_AGE) // _DEFAULT_YEARS_PER_CHAPTER
    chapters: list[ChapterRecord] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    @property
    def end_age(self) -> int:
        return self.start_age + self.end_chapter * self.years_per_chapter

    def can_advance(self) -> bool:
        """已完成章数 < 总章数才放行；满 end_chapter 永久 false（60 岁定格）。"""
        return self.current_chapter < self.end_chapter

    def next_age_range(self) -> str:
        """下一章覆盖的年龄段字符串，如 current_chapter=0、start=5、step=5 → "5-10"。"""
        lo = self.start_age + self.current_chapter * self.years_per_chapter
        hi = lo + self.years_per_chapter
        return f"{lo}-{hi}"

    def advance(self, chapter_result: ChapterRecord) -> None:
        """推进一章：append 产物、current_chapter+1、age 前移、active_persona_chapter 跟到最新。

        调用方有责任先用 can_advance() 判定；满章后再调会 raise（防止越界写脏数据）。
        """
        if not self.can_advance():
            raise ValueError(
                f"卡 {self.card_id} 已满 {self.end_chapter} 章（{self.end_age} 岁定格），不能再 advance"
            )
        self.chapters.append(chapter_result)
        self.current_chapter += 1
        self.age = self.start_age + self.current_chapter * self.years_per_chapter
        # 卡的"当前真身"= 最新章人格（转生/对话装配按它取 persona 目录）
        self.active_persona_chapter = self.current_chapter

    def installed_skill_count(self) -> int:
        """整张卡已自动装上的 skill 总数；forge_runner 据此扣每卡安装预算。"""
        return sum(len(ch.installed_skills) for ch in self.chapters)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ────────── 目录布局 ──────────


def cards_root(gacha_root: Path) -> Path:
    return gacha_root / CARDS_DIRNAME


def card_dir(gacha_root: Path, card_id: str) -> Path:
    return cards_root(gacha_root) / card_id


def card_json_path(gacha_root: Path, card_id: str) -> Path:
    return card_dir(gacha_root, card_id) / CARD_JSON_FILENAME


def biography_dir(gacha_root: Path, card_id: str) -> Path:
    return card_dir(gacha_root, card_id) / BIOGRAPHY_DIRNAME


def biography_path(gacha_root: Path, card_id: str, chapter_no: int) -> Path:
    """第 N 章传记文件——命名唯一权威（写：forge/migrate；读：API），别再裸拼 f-string。"""
    return biography_dir(gacha_root, card_id) / f"chapter-{chapter_no}.md"


def persona_root(gacha_root: Path, card_id: str) -> Path:
    return card_dir(gacha_root, card_id) / PERSONA_DIRNAME


def is_valid_card_id(card_id: str) -> bool:
    return bool(_CARD_ID_RE.match(card_id))


# ────────── 新建 ──────────


def new_card_id() -> str:
    """c<yyyymmdd>-<4位hex>：日期可读 + 随机防撞；同日撞名由 create_card 的存在性重试兜底。"""
    return f"c{time.strftime('%Y%m%d')}-{secrets.token_hex(2)}"


def new_card_state(
    *,
    card_id: str,
    seed: CardSeed,
    start_age: int = _DEFAULT_START_AGE,
    years_per_chapter: int = _DEFAULT_YEARS_PER_CHAPTER,
    end_age: int = _DEFAULT_END_AGE,
    title: str = "",
) -> CardState:
    """按参数造一张全新（0 章、起点年龄）的卡状态；end_chapter 由年龄跨度/每章年数推出。"""
    end_chapter = (end_age - start_age) // years_per_chapter if years_per_chapter else 0
    return CardState(
        card_id=card_id,
        title=title,
        seed=seed,
        age=start_age,
        start_age=start_age,
        years_per_chapter=years_per_chapter,
        end_chapter=max(end_chapter, 0),
    )


def create_card(
    gacha_root: Path,
    seed: CardSeed,
    *,
    start_age: int = _DEFAULT_START_AGE,
    years_per_chapter: int = _DEFAULT_YEARS_PER_CHAPTER,
    end_age: int = _DEFAULT_END_AGE,
    title: str = "",
) -> CardState:
    """抽一张新卡：分配不撞的 card_id、建卡目录、写初始 card.json，返回状态。"""
    cid = new_card_id()
    for _ in range(8):
        if not card_dir(gacha_root, cid).exists():
            break
        cid = new_card_id()
    else:
        # 8 连撞概率约 0；兜底加长随机段，保证必然可用
        cid = f"c{time.strftime('%Y%m%d')}-{secrets.token_hex(4)}"
    state = new_card_state(
        card_id=cid,
        seed=seed,
        start_age=start_age,
        years_per_chapter=years_per_chapter,
        end_age=end_age,
        title=title,
    )
    save_card_state(gacha_root, state)
    # 出身/触发此刻还空（由第1章大模型现写），抽卡这步只定了方向 + 发散种子
    _logger.info(
        "新卡已建",
        card_id=cid,
        genre=seed.genre,
        creativity=seed.creativity,
        divergence=seed.divergence,
    )
    return state


# ────────── load / save ──────────


def load_card_state(gacha_root: Path, card_id: str) -> CardState | None:
    """读卡状态；文件不存在 / 坏 JSON / card_id 非法 → None（**不**静默重置成新卡——
    那会抹掉已锻章计数；卡文件是真相源，坏了由调用方决定报错还是重建）。"""
    if not is_valid_card_id(card_id):
        return None
    path = card_json_path(gacha_root, card_id)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("card.json 解析失败", card_id=card_id, path=str(path), error=str(exc))
        return None
    if not isinstance(raw, dict):
        return None
    return _coerce_card_state(raw, fallback_card_id=card_id)


def save_card_state(gacha_root: Path, state: CardState) -> None:
    """原子写 card.json：先写 .tmp 再 rename，避免半写文件破坏续锻。"""
    path = card_json_path(gacha_root, state.card_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        _logger.error("card.json 写盘失败", card_id=state.card_id, path=str(path), error=str(exc))


# ────────── 防御性解析（坏字段兜底，不抛） ──────────


def coerce_chapters(raw: object) -> list[ChapterRecord]:
    """把 JSON 列表还原成 ChapterRecord 列表；坏条目跳过。migrate 平移旧 growth 章也走这里。"""
    chapters: list[ChapterRecord] = []
    if not isinstance(raw, list):
        return chapters
    for item in raw:
        if not isinstance(item, dict):
            continue
        age_range = item.get("age_range")
        summary = item.get("summary")
        if not isinstance(age_range, str) or not isinstance(summary, str):
            continue
        skills_raw = item.get("installed_skills")
        skills = (
            [s for s in skills_raw if isinstance(s, str)] if isinstance(skills_raw, list) else []
        )
        report = item.get("report")
        created = item.get("created_at")
        chapters.append(
            ChapterRecord(
                age_range=age_range,
                summary=summary,
                report=report if isinstance(report, str) else "",
                installed_skills=skills,
                created_at=float(created) if isinstance(created, int | float) else time.time(),
            )
        )
    return chapters


def _int(raw: dict[str, Any], key: str, default: int) -> int:
    v = raw.get(key)
    return v if isinstance(v, int) and not isinstance(v, bool) else default


def _str(raw: dict[str, Any], key: str, default: str = "") -> str:
    v = raw.get(key)
    return v if isinstance(v, str) else default


def _coerce_status(raw: object) -> CardStatus:
    for s in _VALID_STATUS:
        if raw == s:
            return s
    return "paused"


def _coerce_seed(raw: object) -> CardSeed:
    if not isinstance(raw, dict):
        return CardSeed()
    talents_raw = raw.get("talents")
    talents = (
        [t for t in talents_raw if isinstance(t, str)] if isinstance(talents_raw, list) else []
    )
    creativity_raw = raw.get("creativity")
    creativity = (
        float(creativity_raw)
        if isinstance(creativity_raw, int | float) and not isinstance(creativity_raw, bool)
        else 1.0
    )
    return CardSeed(
        genre=_str(raw, "genre", "random"),
        genre_label=_str(raw, "genre_label", "随机"),
        origin=_str(raw, "origin"),
        talents=talents,
        trigger=_str(raw, "trigger"),
        creativity=min(max(creativity, 0.0), 2.0),
        custom_prompt=_str(raw, "custom_prompt"),
        birth_year=_int(raw, "birth_year", 1992),
        divergence=_int(raw, "divergence", 0),
    )


def _coerce_rarity(raw: object) -> CardRarity:
    if not isinstance(raw, dict):
        return CardRarity()
    return CardRarity(
        grade=_str(raw, "grade"),
        score=_int(raw, "score", 0),
        comment=_str(raw, "comment"),
    )


def _coerce_card_state(raw: dict[str, Any], *, fallback_card_id: str) -> CardState:
    """把 JSON dict 还原成 CardState；缺字段用默认、坏 chapters 跳过（与目录名兜底 card_id）。"""
    card_id = _str(raw, "card_id") or fallback_card_id
    chapters = coerce_chapters(raw.get("chapters"))
    created = raw.get("created_at")
    # age 是 start_age + 章数 × 步长的派生量；缺失/坏值时按派生兜底，
    # 不能落回常量起点（否则"5 岁"挂在"第 7 章"旁边自相矛盾）
    start_age = _int(raw, "start_age", _DEFAULT_START_AGE)
    years_per_chapter = _int(raw, "years_per_chapter", _DEFAULT_YEARS_PER_CHAPTER)
    current_chapter = _int(raw, "current_chapter", len(chapters))
    return CardState(
        card_id=card_id,
        title=_str(raw, "title"),
        status=_coerce_status(raw.get("status")),
        seed=_coerce_seed(raw.get("seed")),
        rarity=_coerce_rarity(raw.get("rarity")),
        current_chapter=current_chapter,
        age=_int(raw, "age", start_age + current_chapter * years_per_chapter),
        active_persona_chapter=_int(raw, "active_persona_chapter", 0),
        start_age=start_age,
        years_per_chapter=years_per_chapter,
        end_chapter=_int(
            raw,
            "end_chapter",
            (_DEFAULT_END_AGE - _DEFAULT_START_AGE) // _DEFAULT_YEARS_PER_CHAPTER,
        ),
        chapters=chapters,
        created_at=float(created) if isinstance(created, int | float) else time.time(),
    )
