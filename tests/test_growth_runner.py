"""成长执行器单测；覆盖结构化输出解析 + 修复重试 + 三态如实上报 + skill 无条件记账 + 正常推进。

不打真 LLM：用最小 FakeEngine 桩，complete_turn 返回预置的 ChatMessage，
.llm.chat 返回预置的修复文本（覆盖 #3 一次 LLM 修复路径）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sanshiliu.engine.loop import TOOL_TURN_LIMIT_MESSAGE
from sanshiliu.engine.types import ChatMessage
from sanshiliu.scheduler.growth_runner import (
    GrowthChapterError,
    GrowthRunner,
    _coerce_chapter_payload,
    _coerce_report,
    _parse_structured_output,
)
from sanshiliu.scheduler.growth_state import load_growth_state
from sanshiliu.skills.loader import SkillLoader


class _ChatReply:
    """带 .text 的最小返回对象（鸭子类型，无需真 StreamResult）。"""

    def __init__(self, text: str) -> None:
        self.text = text


class FakeLLM:
    """最小 llm 桩：只实现 GrowthRunner._repair_structured_output 用到的 chat()。"""

    def __init__(self, repair_text: str = "") -> None:
        self._repair_text = repair_text
        self.chat_calls = 0

    async def chat(self, **_kwargs: Any) -> _ChatReply:
        self.chat_calls += 1
        return _ChatReply(self._repair_text)


class FakeEngine:
    """最小 engine 桩；complete_turn 不跑工具循环，直接返回预置文本。

    on_complete：可选回调，在返回前执行——用于模拟"complete_turn 的 tool 循环里装了 skill"
    （#2 验证降级也记账）。
    """

    def __init__(
        self,
        reply_text: str,
        *,
        repair_text: str = "",
        on_complete: Any = None,
    ) -> None:
        self._reply_text = reply_text
        self.calls = 0
        self.last_max_turns: int | None = None
        self.llm = FakeLLM(repair_text)
        self._on_complete = on_complete

    async def complete_turn(
        self,
        _session: Any,
        _user_text: Any,
        *,
        max_turns: int | None = None,
        on_user_message: Any = None,
    ) -> ChatMessage:
        self.calls += 1
        self.last_max_turns = max_turns
        if self._on_complete is not None:
            self._on_complete()
        return ChatMessage(role="assistant", content=self._reply_text)


def _make_runner(
    engine: Any, tmp_path: Path, *, skill_loader: SkillLoader | None = None
) -> GrowthRunner:
    return GrowthRunner(
        engine=engine,  # type: ignore[arg-type]  测试桩，鸭子类型即可
        growth_state_path=tmp_path / "growth-state.json",
        memdir_dir=tmp_path / "memdir",
        start_age=5,
        years_per_chapter=5,
        end_age=30,
        skill_loader=skill_loader,
    )


def _valid_payload_text() -> str:
    payload = {
        "narrative": "5 到 10 岁，我从三十六贱笑的底色长成了爱写段子的小学生。",
        "age_range": "5-10",
        "learned": ["写打油诗", "逗人笑"],
        "personality": "一个早慧又贫嘴的孩子，开始把生活编成段子。",
        "report": "主人，我这五年从贱笑的底色长成了爱写段子的小学生。",
        "skill_intents": [{"domain": "脱口秀", "why": "这一章迷上了逗人笑"}],
    }
    return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


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


# ── #3 schema 校验/兜底（纯函数）────────────────────────────────────


def test_coerce_payload_empty_narrative_raises() -> None:
    # narrative 缺失 / 空 / 非串 → 硬失败（本章没有真内容可落盘）
    with pytest.raises(GrowthChapterError):
        _coerce_chapter_payload({"age_range": "5-10"})
    with pytest.raises(GrowthChapterError):
        _coerce_chapter_payload({"narrative": "   "})
    with pytest.raises(GrowthChapterError):
        _coerce_chapter_payload({"narrative": 123})


def test_coerce_payload_fills_missing_arrays_and_objects() -> None:
    # 数组/对象/串字段缺失或类型错 → 兜底空值，不让畸形类型流到传记/状态
    out = _coerce_chapter_payload(
        {"narrative": "长大了", "learned": "不是数组", "persona": "不是字典"}
    )
    assert out["narrative"] == "长大了"
    assert out["learned"] == []
    assert out["skill_intents"] == []
    assert out["persona"] == {}
    assert out["personality"] == ""


# ── #4 turn 上限可配 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_growth_passes_high_max_turns(tmp_path: Path) -> None:
    # GrowthRunner 调 complete_turn 时显式传更高的 max_turns（成长一章步骤多）
    engine = FakeEngine(_valid_payload_text())
    runner = _make_runner(engine, tmp_path)

    await runner({})

    assert engine.last_max_turns is not None
    assert engine.last_max_turns > 6


def test_complete_turn_default_max_turns_stays_six() -> None:
    # #4：complete_turn 不传 max_turns 时仍沿用默认 6（非 growth 通道行为字节不变）
    import inspect

    from sanshiliu.engine import loop as loop_mod

    assert loop_mod._DEFAULT_MAX_TURNS == 6
    sig = inspect.signature(loop_mod.ConversationEngine.complete_turn)
    assert sig.parameters["max_turns"].default is None


# ── #3 端到端：修复成功 / 修复仍坏 raise / narrative 空 raise ──────────


@pytest.mark.asyncio
async def test_malformed_then_repair_succeeds_and_advances(tmp_path: Path) -> None:
    # 首轮畸形（尾逗号未闭合）→ 一次 LLM 修复重发合法 JSON → 推进成功
    bad = '{"narrative": "我长成博主了", "age_range": "5-10",'  # 尾逗号 + 未闭合
    good = json.dumps(
        {"narrative": "我长成了爱写段子的小学生", "age_range": "5-10"},
        ensure_ascii=False,
    )
    engine = FakeEngine(bad, repair_text=good)
    runner = _make_runner(engine, tmp_path)

    await runner({})

    assert engine.llm.chat_calls == 1  # 走了一次修复
    state = load_growth_state(tmp_path / "growth-state.json")
    assert state.current_chapter == 1  # 修复后推进
    assert "小学生" in state.chapters[0].summary


@pytest.mark.asyncio
async def test_repair_still_bad_raises_and_does_not_advance(tmp_path: Path) -> None:
    # 首轮畸形 + 修复仍畸形 → 硬失败 raise、不推进状态
    engine = FakeEngine("完全没有 JSON 的一段话", repair_text="修复也没给 JSON")
    runner = _make_runner(engine, tmp_path)

    with pytest.raises(GrowthChapterError):
        await runner({})

    assert engine.llm.chat_calls == 1
    state = load_growth_state(tmp_path / "growth-state.json")
    assert state.current_chapter == 0


@pytest.mark.asyncio
async def test_empty_narrative_after_parse_raises(tmp_path: Path) -> None:
    # JSON 合法但 narrative 空 → schema 校验硬失败 raise、不推进
    engine = FakeEngine('{"narrative": "", "age_range": "5-10"}')
    runner = _make_runner(engine, tmp_path)

    with pytest.raises(GrowthChapterError):
        await runner({})

    state = load_growth_state(tmp_path / "growth-state.json")
    assert state.current_chapter == 0


# ── #4 触顶（turn-limit 哨兵）→ 硬失败 raise，不喂修复 ────────────────


@pytest.mark.asyncio
async def test_turn_limit_sentinel_raises_without_repair(tmp_path: Path) -> None:
    # complete_turn 触顶返回固定文案 → 识别为硬失败 raise，且**不**调修复
    engine = FakeEngine(TOOL_TURN_LIMIT_MESSAGE, repair_text="不该被用到")
    runner = _make_runner(engine, tmp_path)

    with pytest.raises(GrowthChapterError):
        await runner({})

    assert engine.llm.chat_calls == 0  # 触顶不喂给 JSON 修复
    state = load_growth_state(tmp_path / "growth-state.json")
    assert state.current_chapter == 0


# ── #1 三态如实上报：降级 raise / 已定格 ok / 已推进 ok + result_message ──


@pytest.mark.asyncio
async def test_degrade_propagates_so_heartbeat_marks_error(tmp_path: Path) -> None:
    # __call__ 不再吞致命降级：修复后仍坏 → 上抛（heartbeat._execute 会据此标 error）
    engine = FakeEngine("没有 JSON", repair_text="还是没有")
    runner = _make_runner(engine, tmp_path)
    ctx: dict[str, Any] = {}

    with pytest.raises(GrowthChapterError):
        await runner(ctx)

    # 失败路径不写 result_message（heartbeat 会走 error 分支而非 ok）
    assert "result_message" not in ctx


@pytest.mark.asyncio
async def test_frozen_state_returns_ok_with_message(tmp_path: Path) -> None:
    # 已定格 = 合法 no-op：不调 engine、正常返回、ctx 带"已定格"人话（heartbeat 标 ok）
    path = tmp_path / "growth-state.json"
    from sanshiliu.scheduler.growth_state import ChapterRecord, GrowthState, save_growth_state

    state = GrowthState()
    for i in range(5):
        state.advance(ChapterRecord(age_range=f"ch{i}", summary=f"第{i + 1}章"))
    save_growth_state(path, state)

    engine = FakeEngine('{"narrative": "n", "age_range": "30-35"}')
    runner = _make_runner(engine, tmp_path)
    ctx: dict[str, Any] = {}
    await runner(ctx)

    assert engine.calls == 0  # 定格 → 不跑 engine
    assert "定格" in ctx["result_message"]


@pytest.mark.asyncio
async def test_advanced_returns_ok_with_chapter_message(tmp_path: Path) -> None:
    # 已推进 = 正常返回，result_message 区分"第 N 章已完成"（而非笼统"完成"）
    engine = FakeEngine(_valid_payload_text())
    runner = _make_runner(engine, tmp_path)
    ctx: dict[str, Any] = {}
    await runner(ctx)

    state = load_growth_state(tmp_path / "growth-state.json")
    assert state.current_chapter == 1
    assert state.age == 10
    assert "第 1 章已完成" in ctx["result_message"]


@pytest.mark.asyncio
async def test_engine_exception_propagates(tmp_path: Path) -> None:
    # complete_turn 抛 → 包成 GrowthChapterError 上抛（heartbeat 标 error），不推进
    class BoomEngine:
        calls = 0
        llm = FakeLLM()

        async def complete_turn(
            self, _session: Any, _user_text: Any, *, max_turns: int | None = None,
            on_user_message: Any = None,
        ) -> ChatMessage:
            BoomEngine.calls += 1
            raise RuntimeError("LLM 炸了")

    runner = _make_runner(BoomEngine(), tmp_path)
    with pytest.raises(GrowthChapterError):
        await runner({})

    state = load_growth_state(tmp_path / "growth-state.json")
    assert state.current_chapter == 0


# ── 正常推进落盘（保留原有覆盖）────────────────────────────────────


@pytest.mark.asyncio
async def test_valid_output_advances_and_writes_biography(tmp_path: Path) -> None:
    engine = FakeEngine(_valid_payload_text())
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


# ── #2 已装 skill 无条件记账（即使本章降级）────────────────────────


def _seed_skill(skills_root: Path, skill_id: str) -> None:
    """在 skills/<id>/SKILL.md 写一个最小可被 SkillLoader 解析的 skill（模拟安装产物）。"""
    d = skills_root / skill_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {skill_id}\ndescription: 测试技能\n---\n正文\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_degrade_still_accounts_installed_skills(tmp_path: Path) -> None:
    # #2：安装发生在 complete_turn 的 tool 循环里（先于解析）；本章解析降级也要把已装 skill diff 出来。
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    loader = SkillLoader([skills_root])
    loader.load()  # 装前快照基线（空）

    # 模拟 complete_turn 期间装了一个 skill，然后返回畸形输出（触发降级）
    def _install() -> None:
        _seed_skill(skills_root, "tuokouxiu")

    engine = FakeEngine(
        "没有 JSON 的降级输出", repair_text="修复也没给", on_complete=_install
    )
    runner = _make_runner(engine, tmp_path, skill_loader=loader)

    with pytest.raises(GrowthChapterError):
        await runner({})

    # 降级仍 raise（不推进），但 skill 记账（目录 diff）已无条件跑过：loader 被 invalidate+reload，
    # 新装的 skill 已被扫描到（审计日志已捕获）。验证 loader 现在能看到它（diff 确实执行）。
    after_ids = {s.id for s in loader.list()}
    assert "tuokouxiu" in after_ids

    state = load_growth_state(tmp_path / "growth-state.json")
    assert state.current_chapter == 0  # 降级不推进


@pytest.mark.asyncio
async def test_advance_records_installed_skills(tmp_path: Path) -> None:
    # 成功路径：装的 skill 记入 ChapterRecord.installed_skills 并随状态落盘
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    loader = SkillLoader([skills_root])
    loader.load()

    def _install() -> None:
        _seed_skill(skills_root, "duanzi")

    engine = FakeEngine(_valid_payload_text(), on_complete=_install)
    runner = _make_runner(engine, tmp_path, skill_loader=loader)

    await runner({})

    state = load_growth_state(tmp_path / "growth-state.json")
    assert state.current_chapter == 1
    assert state.chapters[0].installed_skills == ["duanzi"]
