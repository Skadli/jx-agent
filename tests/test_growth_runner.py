"""成长执行器单测；覆盖结构化输出解析 + 畸形降级（不推进状态）+ 正常落盘推进。

不打真 LLM：用一个最小 FakeEngine 桩，complete_turn 返回预置的 ChatMessage。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sanshiliu.engine.types import ChatMessage
from sanshiliu.scheduler.growth_runner import (
    GrowthRunner,
    _coerce_report,
    _parse_structured_output,
)
from sanshiliu.scheduler.growth_state import load_growth_state


class FakeEngine:
    """最小 engine 桩；complete_turn 不跑工具循环，直接返回预置文本。"""

    def __init__(self, reply_text: str) -> None:
        self._reply_text = reply_text
        self.calls = 0

    async def complete_turn(self, _session: Any, _user_text: Any) -> ChatMessage:
        self.calls += 1
        return ChatMessage(role="assistant", content=self._reply_text)


def _make_runner(engine: Any, tmp_path: Path) -> GrowthRunner:
    return GrowthRunner(
        engine=engine,  # type: ignore[arg-type]  测试桩，鸭子类型即可
        growth_state_path=tmp_path / "growth-state.json",
        memdir_dir=tmp_path / "memdir",
        start_age=5,
        years_per_chapter=5,
        end_age=30,
    )


# ── 纯解析层 ──────────────────────────────────────────────────────────


def test_parse_plain_json_object() -> None:
    raw = '{"narrative": "长大了", "age_range": "5-10", "learned": [], "skill_intents": []}'
    parsed = _parse_structured_output(raw)
    assert parsed is not None
    assert parsed["narrative"] == "长大了"


def test_parse_fenced_json_block() -> None:
    raw = '前面有废话\n```json\n{"narrative": "x", "age_range": "5-10"}\n```\n后面也有'
    parsed = _parse_structured_output(raw)
    assert parsed is not None
    assert parsed["age_range"] == "5-10"


def test_parse_braces_substring_fallback() -> None:
    raw = '好的，这是结果：{"narrative": "y", "learned": ["a"]} 完毕'
    parsed = _parse_structured_output(raw)
    assert parsed is not None
    assert parsed["learned"] == ["a"]


def test_parse_malformed_returns_none() -> None:
    assert _parse_structured_output("这一章我长成了博主，但是没有给 JSON") is None
    assert _parse_structured_output("") is None
    assert _parse_structured_output("{ 坏的 json 没闭合") is None


def test_parse_non_dict_json_returns_none() -> None:
    # 合法 JSON 但不是对象（是数组）→ 视为失败
    assert _parse_structured_output("[1, 2, 3]") is None


def test_coerce_report_uses_value_or_fallback() -> None:
    # 给了非空 report → 用它；缺失 / 空串 → 回落 narrative（dashboard 汇报栏不空白）
    assert _coerce_report("主人，我长成博主了", fallback="叙述") == "主人，我长成博主了"
    assert _coerce_report(None, fallback="叙述兜底") == "叙述兜底"
    assert _coerce_report("   ", fallback="叙述兜底") == "叙述兜底"
    assert _coerce_report(123, fallback="叙述兜底") == "叙述兜底"


# ── 端到端：降级不推进 / 正常推进落盘 ────────────────────────────────


@pytest.mark.asyncio
async def test_malformed_output_does_not_advance_state(tmp_path: Path) -> None:
    engine = FakeEngine("我长成了一个博主，可惜忘了输出 JSON")
    runner = _make_runner(engine, tmp_path)

    await runner({})

    # 畸形输出 → 不推进状态、不写传记
    state = load_growth_state(tmp_path / "growth-state.json")
    assert state.current_chapter == 0
    assert engine.calls == 1
    memdir = tmp_path / "memdir"
    biographies = list(memdir.glob("*growth-chapter-*.md")) if memdir.is_dir() else []
    assert biographies == []


@pytest.mark.asyncio
async def test_valid_output_advances_and_writes_biography(tmp_path: Path) -> None:
    payload = {
        "narrative": "5 到 10 岁，我从三十六贱笑的底色长成了爱写段子的小学生。",
        "age_range": "5-10",
        "learned": ["写打油诗", "逗人笑"],
        "personality": "一个早慧又贫嘴的孩子，开始把生活编成段子。",
        "report": "主人，我这五年从贱笑的底色长成了爱写段子的小学生。",
        "skill_intents": [{"domain": "脱口秀", "why": "这一章迷上了逗人笑"}],
    }
    engine = FakeEngine("```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```")
    runner = _make_runner(engine, tmp_path)

    await runner({})

    # 正常输出 → 推进到第 1 章、age=10、写出传记文件
    state = load_growth_state(tmp_path / "growth-state.json")
    assert state.current_chapter == 1
    assert state.age == 10
    assert state.chapters[0].age_range == "5-10"
    assert "小学生" in state.chapters[0].summary
    # R8：report 落进状态供 dashboard 展示
    assert state.chapters[0].report == "主人，我这五年从贱笑的底色长成了爱写段子的小学生。"

    biographies = list((tmp_path / "memdir").glob("*growth-chapter-1*.md"))
    assert len(biographies) == 1
    body = biographies[0].read_text(encoding="utf-8")
    assert "growth-chapter-1" in body  # frontmatter name
    assert "写打油诗" in body  # learned 也落进传记便于回看
    assert "早慧又贫嘴" in body  # personality 摘要


@pytest.mark.asyncio
async def test_frozen_state_skips_engine(tmp_path: Path) -> None:
    # 预置一个已满 5 章的状态：定格后连 engine 都不该调
    path = tmp_path / "growth-state.json"
    from sanshiliu.scheduler.growth_state import ChapterRecord, GrowthState, save_growth_state

    state = GrowthState()
    for i in range(5):
        state.advance(ChapterRecord(age_range=f"ch{i}", summary=f"第{i + 1}章"))
    save_growth_state(path, state)

    engine = FakeEngine('{"narrative": "n", "age_range": "30-35"}')
    runner = _make_runner(engine, tmp_path)
    await runner({})

    assert engine.calls == 0  # 定格 → 不跑 engine
    reloaded = load_growth_state(path)
    assert reloaded.current_chapter == 5


@pytest.mark.asyncio
async def test_engine_exception_does_not_advance(tmp_path: Path) -> None:
    class BoomEngine:
        calls = 0

        async def complete_turn(self, _session: Any, _user_text: Any) -> ChatMessage:
            BoomEngine.calls += 1
            raise RuntimeError("LLM 炸了")

    runner = _make_runner(BoomEngine(), tmp_path)
    # 异常被吞掉、不冒泡（后台任务不能崩）；状态不推进
    await runner({})

    state = load_growth_state(tmp_path / "growth-state.json")
    assert state.current_chapter == 0
