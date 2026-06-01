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
    """最小 engine 桩；complete_turn 不跑真工具循环，直接返回预置文本。

    方案 A 两段：phase-1（use_tools=False）产传记 JSON；phase-2（use_tools=True）装 skill。
    本桩对两段都返回 reply_text，但用 use_tools 区分记录调用，并只在 phase-2（use_tools=True）跑
    on_install——模拟"装 skill 在 phase-2 的 tool 循环里发生"。phase1_max_turns/phase2_max_turns
    分别记录两段的 max_turns。

    on_install：可选回调，仅在 phase-2 返回前执行（模拟装了一个 skill）。
    phase2_raises：True 时 phase-2 抛异常（验证 phase-2 失败不回退已成立的章）。
    """

    def __init__(
        self,
        reply_text: str,
        *,
        repair_text: str = "",
        on_install: Any = None,
        phase2_raises: bool = False,
    ) -> None:
        self._reply_text = reply_text
        self.calls = 0
        self.phase1_calls = 0
        self.phase2_calls = 0
        self.last_max_turns: int | None = None
        self.phase1_max_turns: int | None = None
        self.phase2_max_turns: int | None = None
        self.phase1_use_tools: bool | None = None
        self.llm = FakeLLM(repair_text)
        self._on_install = on_install
        self._phase2_raises = phase2_raises

    async def complete_turn(
        self,
        _session: Any,
        _user_text: Any,
        *,
        max_turns: int | None = None,
        on_user_message: Any = None,
        use_tools: bool = True,
    ) -> ChatMessage:
        self.calls += 1
        self.last_max_turns = max_turns
        if use_tools:
            # phase-2：装 skill 段
            self.phase2_calls += 1
            self.phase2_max_turns = max_turns
            if self._phase2_raises:
                raise RuntimeError("phase-2 装 skill 炸了")
            if self._on_install is not None:
                self._on_install()
            return ChatMessage(role="assistant", content="装好了/都跳过了")
        # phase-1：传记段（零工具）
        self.phase1_calls += 1
        self.phase1_max_turns = max_turns
        self.phase1_use_tools = use_tools
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
async def test_phase1_runs_tool_free_with_its_own_budget(tmp_path: Path) -> None:
    # 方案 A：phase-1 必须 use_tools=False（传记零工具），且用 phase-1 的小预算（不是 phase-2 的）。
    # 不传 skill_loader → phase-2 跳过，故只跑 phase-1 这一段。
    from sanshiliu.scheduler.growth_runner import _GROWTH_PHASE1_MAX_TURNS

    engine = FakeEngine(_valid_payload_text())
    runner = _make_runner(engine, tmp_path)

    await runner({})

    assert engine.phase1_calls == 1
    assert engine.phase1_use_tools is False  # 传记段无条件不挂工具
    assert engine.phase1_max_turns == _GROWTH_PHASE1_MAX_TURNS
    assert engine.phase2_calls == 0  # 无 skill_loader → phase-2 不跑


def test_complete_turn_default_unchanged_for_non_growth_callers() -> None:
    # #4 + 方案 A：complete_turn 不传 max_turns 仍沿用默认 6；use_tools 默认 True（非 growth 通道字节不变）
    import inspect

    from sanshiliu.engine import loop as loop_mod

    assert loop_mod._DEFAULT_MAX_TURNS == 6
    sig = inspect.signature(loop_mod.ConversationEngine.complete_turn)
    assert sig.parameters["max_turns"].default is None
    assert sig.parameters["use_tools"].default is True


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
async def test_phase1_engine_exception_propagates(tmp_path: Path) -> None:
    # phase-1 complete_turn 抛 → 包成 GrowthChapterError 上抛（heartbeat 标 error），不推进
    class BoomEngine:
        calls = 0
        llm = FakeLLM()

        async def complete_turn(
            self, _session: Any, _user_text: Any, *, max_turns: int | None = None,
            on_user_message: Any = None, use_tools: bool = True,
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


# ── 方案 A phase-2：装 skill 记账（成功）+ phase-2 失败绝不回退已成立的章 ────────────


def _seed_skill(skills_root: Path, skill_id: str) -> None:
    """在 skills/<id>/SKILL.md 写一个最小可被 SkillLoader 解析的 skill（模拟安装产物）。"""
    d = skills_root / skill_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {skill_id}\ndescription: 测试技能\n---\n正文\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_phase2_records_installed_skills_after_advance(tmp_path: Path) -> None:
    # 成功路径：phase-1 先推进章，phase-2 装的 skill 经目录 diff 回填到本章 installed_skills 并落盘
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    loader = SkillLoader([skills_root])
    loader.load()

    def _install() -> None:
        _seed_skill(skills_root, "duanzi")

    engine = FakeEngine(_valid_payload_text(), on_install=_install)
    runner = _make_runner(engine, tmp_path, skill_loader=loader)

    await runner({})

    assert engine.phase1_calls == 1
    assert engine.phase2_calls == 1  # 有 skill_intent + loader → phase-2 跑了
    state = load_growth_state(tmp_path / "growth-state.json")
    assert state.current_chapter == 1
    assert state.chapters[0].installed_skills == ["duanzi"]


@pytest.mark.asyncio
async def test_phase2_failure_does_not_revert_advanced_chapter(tmp_path: Path) -> None:
    # 复现反转（PRD 核心）：phase-2 装 skill 全程抛异常时，本章传记仍产出、状态仍推进、不抛。
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    loader = SkillLoader([skills_root])
    loader.load()

    engine = FakeEngine(_valid_payload_text(), phase2_raises=True)
    runner = _make_runner(engine, tmp_path, skill_loader=loader)
    ctx: dict[str, Any] = {}

    await runner(ctx)  # phase-2 炸了也不上抛——章照常成立

    assert engine.phase2_calls == 1  # phase-2 确实跑了（并抛了，被吞）
    state = load_growth_state(tmp_path / "growth-state.json")
    assert state.current_chapter == 1  # 章已推进，绝不被 phase-2 回退
    assert state.chapters[0].installed_skills == []  # 没装上 → 空，但章成立
    assert "第 1 章已完成" in ctx["result_message"]  # heartbeat 仍视为成功


@pytest.mark.asyncio
async def test_phase2_skipped_when_no_skill_intents(tmp_path: Path) -> None:
    # 本章无 skill_intent → phase-2 直接跳过（不跑第二次 complete_turn），章照常推进
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    loader = SkillLoader([skills_root])
    loader.load()

    payload = {"narrative": "长大了，但这章没提出技能意图", "age_range": "5-10"}
    engine = FakeEngine(
        "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
    )
    runner = _make_runner(engine, tmp_path, skill_loader=loader)

    await runner({})

    assert engine.phase1_calls == 1
    assert engine.phase2_calls == 0  # 无意图 → phase-2 跳过
    state = load_growth_state(tmp_path / "growth-state.json")
    assert state.current_chapter == 1


# ── #2 phase-2 安装 prompt 据实点名落点（不再硬写 ./.sanshiliu/skills / 项目 skills 目录）────────


def test_install_prompt_names_resolved_global_dir(tmp_path: Path) -> None:
    # #2 复现：旧 prompt 硬写"装进项目 skills 目录（默认 ./.sanshiliu/skills）"，和实际落点（用户级全局
    # 目录）错位。传入 skills_dir_global 后，prompt 必须点名这条绝对路径，且不含旧的项目级措辞。
    global_dir = tmp_path / "twin" / "skills"
    runner = GrowthRunner(
        engine=FakeEngine(_valid_payload_text()),  # type: ignore[arg-type]  桩，鸭子类型即可
        growth_state_path=tmp_path / "growth-state.json",
        memdir_dir=tmp_path / "memdir",
        start_age=5,
        years_per_chapter=5,
        end_age=30,
        skills_dir_global=global_dir,
    )

    prompt = runner._build_install_prompt(
        1, "5-10", [{"domain": "脱口秀", "why": "迷上逗人笑"}]
    )

    assert str(global_dir) in prompt  # 据实点名 installer 真正的落点
    assert "./.sanshiliu/skills" not in prompt  # 旧硬编码项目级路径绝迹
    assert "项目 skills 目录" not in prompt  # 旧措辞绝迹
    assert "用户级全局" in prompt  # 新措辞


def test_install_prompt_falls_back_when_global_dir_missing(tmp_path: Path) -> None:
    # 不传 skills_dir_global（单测/旧调用点）→ prompt 退回不点名具体目录，但仍说"用户级全局"、
    # 仍不出现旧项目级措辞（兜底分支也不能漏回 ./.sanshiliu/skills）。
    runner = _make_runner(FakeEngine(_valid_payload_text()), tmp_path)

    prompt = runner._build_install_prompt(
        1, "5-10", [{"domain": "脱口秀", "why": "迷上逗人笑"}]
    )

    assert "用户级全局" in prompt
    assert "./.sanshiliu/skills" not in prompt
    assert "项目 skills 目录" not in prompt


@pytest.mark.asyncio
async def test_phase2_install_cap_respected(tmp_path: Path) -> None:
    # 每章装 skill 上限：给超过 cap 的 skill_intents，只有 ≤cap 个被带进 phase-2 prompt
    from sanshiliu.scheduler import growth_runner as gr_mod

    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    loader = SkillLoader([skills_root])
    loader.load()

    over_cap = gr_mod._GROWTH_SKILL_INSTALL_CAP + 2
    payload = {
        "narrative": "长大了，这章贪心提了一堆技能意图。",
        "age_range": "5-10",
        "skill_intents": [
            {"domain": f"领域{i}", "why": f"理由{i}"} for i in range(over_cap)
        ],
    }
    engine = FakeEngine(
        "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
    )
    runner = _make_runner(engine, tmp_path, skill_loader=loader)

    # 直接验证截断函数（runner 内部按它截）：超过 cap 的意图被砍到 cap
    capped = runner._cap_skill_intents(payload["skill_intents"])
    assert len(capped) == gr_mod._GROWTH_SKILL_INSTALL_CAP

    await runner({})
    assert engine.phase2_calls == 1  # 有意图 → 仍跑 phase-2（只是带进去的意图被截断）
    state = load_growth_state(tmp_path / "growth-state.json")
    assert state.current_chapter == 1
