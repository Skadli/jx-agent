"""跑完定级：把整生各章浓缩喂给 LLM 评委，产 {grade, score, comment, title}（best-effort）。

已拍板边界（设计 §2 决策 #2）：种子纯随机、**演化结束后**才评级——"开盲盒"感，不配平
卡池、不设概率表。评级是锻造完成的附加产物：任何失败（调用失败/JSON 畸形/字段非法）
都只记日志并返回空评级，**绝不让评级失败否定已锻成的 11 章**。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sanshiliu.foundation.logging import get_logger
from sanshiliu.gacha.card_state import CardRarity, CardState
from sanshiliu.gacha.structured import parse_structured_output

if TYPE_CHECKING:
    from sanshiliu.llm.client import LLMClient
    from sanshiliu.llm.router import LLMRouter

_logger = get_logger(__name__)

GRADES: tuple[str, ...] = ("N", "R", "SR", "SSR")

_GACHA_CHANNEL = "gacha"
_GACHA_USER_ID = "gacha"

# 每章浓缩进评委 prompt 的最大字符数（优先 report 口语版，缺则截 summary）；
# 11 章 × 300 ≈ 3.3k 字，评委一眼看全又不烧上下文。
_DIGEST_CHARS_PER_CHAPTER = 300
_TITLE_MAX_CHARS = 24


async def grade_card(
    llm: LLMClient | LLMRouter,
    state: CardState,
    *,
    session_id: str,
) -> tuple[CardRarity, str]:
    """评一张已锻满的卡，返回 (评级, 卡名)；任何失败 → (空 CardRarity, "")。

    评委走低温 simple chat（无工具、单轮），rubric 三维：成就高度 / 戏剧性 / 自洽圆满。
    """
    digest = _life_digest(state)
    if not digest:
        _logger.warning("评级跳过：卡没有任何章可评", card_id=state.card_id)
        return CardRarity(), ""
    instruction = _build_judge_prompt(state, digest)
    try:
        result = await llm.chat(
            messages=[{"role": "user", "content": instruction}],
            session_id=session_id,
            channel=_GACHA_CHANNEL,
            user_id=_GACHA_USER_ID,
            temperature=0.3,
        )
    except Exception as exc:
        _logger.warning(
            "评级 LLM 调用失败（卡照常定格，留空评级）", card_id=state.card_id, error=str(exc)
        )
        return CardRarity(), ""
    parsed = parse_structured_output(result.text)
    if parsed is None:
        _logger.warning(
            "评级输出无法解析（卡照常定格，留空评级）",
            card_id=state.card_id,
            raw_preview=result.text[:200],
        )
        return CardRarity(), ""

    grade = _coerce_grade(parsed.get("grade"))
    score = _coerce_score(parsed.get("score"))
    comment_raw = parsed.get("comment")
    comment = comment_raw.strip() if isinstance(comment_raw, str) else ""
    title_raw = parsed.get("title")
    title = title_raw.strip()[:_TITLE_MAX_CHARS] if isinstance(title_raw, str) else ""
    if not grade:
        _logger.warning(
            "评级 grade 非法（留空评级，卡名仍可用）",
            card_id=state.card_id,
            raw=parsed.get("grade"),
        )
        return CardRarity(), title
    _logger.info(
        "评级完成", card_id=state.card_id, grade=grade, score=score, title=title or state.title
    )
    return CardRarity(grade=grade, score=score, comment=comment), title


def _life_digest(state: CardState) -> str:
    """各章浓缩：优先 report（面向人的口语汇报），缺则截 summary 开头。"""
    parts: list[str] = []
    for i, ch in enumerate(state.chapters, 1):
        body = (ch.report or ch.summary).strip().replace("\n", " ")
        if len(body) > _DIGEST_CHARS_PER_CHAPTER:
            body = body[:_DIGEST_CHARS_PER_CHAPTER] + "…"
        parts.append(f"第 {i} 章（{ch.age_range} 岁）：{body}")
    return "\n".join(parts)


def _build_judge_prompt(state: CardState, digest: str) -> str:
    seed = state.seed
    skills = sorted({sid for ch in state.chapters for sid in ch.installed_skills})
    lines = [
        "你是「人生卡池」的评级官。下面是一张人生卡从头到尾的完整一生（按章浓缩）。",
        f"命运种子：世界类型={seed.genre_label}；出身={seed.origin or '未注明'}；"
        f"天赋={'、'.join(seed.talents) if seed.talents else '无'}；"
        f"触发事件={seed.trigger or '无'}。",
        f"这一生途中习得并装备的真实技能：{('、'.join(skills)) if skills else '无'}。",
        "",
        digest,
        "",
        "请综合三个维度评定这一生：",
        "- 成就高度：最终走到了哪一步、在其世界里有多大分量；",
        "- 戏剧性：命运转折、奇遇、张力——平淡流水账给低分；",
        "- 自洽圆满：前后承接是否扎实、伏笔是否回收、结局立不立得住。",
        "评级映射：SSR=传奇一生（85-100 分）；SR=精彩一生（70-84）；R=有亮点的普通一生（50-69）；N=平淡一生（0-49）。",
        "再给这张卡起一个有记忆点的卡名（≤12 字，浓缩这一生，例如「金手指渔村仙尊」「开放麦地下王」）。",
        "只输出一个 JSON 对象，不要任何解释、不要 markdown 代码围栏：",
        '{"grade": "SSR|SR|R|N", "score": 0-100 的整数, "comment": "一句话评语", "title": "卡名"}',
    ]
    return "\n".join(lines)


def _coerce_grade(raw: object) -> str:
    if not isinstance(raw, str):
        return ""
    grade = raw.strip().upper()
    return grade if grade in GRADES else ""


def _coerce_score(raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return 0
    return min(max(int(raw), 0), 100)
