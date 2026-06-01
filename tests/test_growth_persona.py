"""成长人格整体演化（PR2）单测：覆盖 override provider 解析、切章生效、base core 零写、

承接（partial persona 承接前章）。不打真 LLM——用最小 FakeEngine 桩（同 test_growth_runner）。

被测不变量：
- provider 返回 chapter 目录 → PersonaLoader.get().to_system_prompt() 反映演化人格；
  provider 返回 None / 目录缺失 → 回落 base core（守卫不破坏 "core 必非空"）。
- 切换 active_persona_chapter → 装配出的 system prompt 随之变化。
- 一次成长 run 全程不改 base persona/core/*.md。
- partial persona（只给部分段落）→ 缺的段落承接上一章，核心永不为空。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sanshiliu.engine.types import ChatMessage
from sanshiliu.identity.loader import PersonaLoader
from sanshiliu.identity.types import CORE_DIRNAME
from sanshiliu.scheduler.growth_persona import (
    chapter_persona_dir,
    make_active_core_provider,
)
from sanshiliu.scheduler.growth_runner import GrowthRunner
from sanshiliu.scheduler.growth_state import load_growth_state, save_growth_state

# ── fixtures：在 tmp_path 下搭一份 base persona/core ──────────────────────


# base core 的五份文件初始内容；用可识别的中文片段方便断言
_BASE_CORE: dict[str, str] = {
    "identity.md": "我是三十六贱笑，一个爱讲段子的博主。",
    "personality.md": "贫嘴、早慧、爱玩梗。",
    "beliefs.md": "把生活编成段子。",
    "style.md": "口语化，爱用 <MSG> 拆多条。",
    "fewshot_short.md": "用户：在吗\n贱笑：永远在。",
}


def _make_base_persona(tmp_path: Path) -> Path:
    """在 tmp_path/persona/core 下写出 base core 五份文件，返回 persona 根目录。"""
    persona_dir = tmp_path / "persona"
    core = persona_dir / CORE_DIRNAME
    core.mkdir(parents=True, exist_ok=True)
    for name, text in _BASE_CORE.items():
        (core / name).write_text(text, encoding="utf-8")
    return persona_dir


def _read_base_core(persona_dir: Path) -> dict[str, str]:
    """快照当前 base core 全部文件内容；用来断言"零写"。"""
    core = persona_dir / CORE_DIRNAME
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(core.glob("*.md"))}


class FakeEngine:
    """最小 engine 桩；complete_turn 直接返回预置文本（同 test_growth_runner）。"""

    def __init__(self, reply_text: str) -> None:
        self._reply_text = reply_text
        self.calls = 0

    async def complete_turn(self, _session: Any, _user_text: Any) -> ChatMessage:
        self.calls += 1
        return ChatMessage(role="assistant", content=self._reply_text)


def _make_runner(
    engine: Any, tmp_path: Path, persona_dir: Path, loader: PersonaLoader | None = None
) -> GrowthRunner:
    """造一个带 PR2 人格演化参数的 GrowthRunner。"""
    return GrowthRunner(
        engine=engine,  # type: ignore[arg-type]  鸭子类型测试桩
        growth_state_path=tmp_path / "growth-state.json",
        memdir_dir=tmp_path / "memdir",
        start_age=5,
        years_per_chapter=5,
        end_age=30,
        persona_dir=persona_dir,
        data_dir=tmp_path,
        persona_loader=loader,
    )


def _payload(persona: dict[str, str] | None, narrative: str = "我长大了") -> str:
    """拼一个合法的成长结构化输出（fenced JSON）。"""
    obj: dict[str, Any] = {
        "narrative": narrative,
        "age_range": "5-10",
        "learned": ["写段子"],
        "personality": "一句话摘要",
        "skill_intents": [],
    }
    if persona is not None:
        obj["persona"] = persona
    return "```json\n" + json.dumps(obj, ensure_ascii=False) + "\n```"


# ── provider 解析：chapter 目录 vs None / 缺失回落 base ────────────────────


def test_provider_none_falls_back_to_base_core(tmp_path: Path) -> None:
    persona_dir = _make_base_persona(tmp_path)
    # 无 growth-state.json → provider 返回 None → loader 用 base core
    provider = make_active_core_provider(tmp_path / "growth-state.json", tmp_path)
    loader = PersonaLoader(persona_dir, active_core_provider=provider)

    prompt = loader.get().to_system_prompt()
    assert "三十六贱笑" in prompt  # base identity


def test_provider_missing_dir_guard_falls_back_to_base(tmp_path: Path) -> None:
    persona_dir = _make_base_persona(tmp_path)
    # state 指向 chapter-3，但该目录根本没写过 → 守卫回落 base，不抛 ConfigError
    state = load_growth_state(tmp_path / "growth-state.json")
    state.active_persona_chapter = 3
    save_growth_state(tmp_path / "growth-state.json", state)

    provider = make_active_core_provider(tmp_path / "growth-state.json", tmp_path)
    loader = PersonaLoader(persona_dir, active_core_provider=provider)

    prompt = loader.get().to_system_prompt()
    assert "三十六贱笑" in prompt  # 守卫生效 → base core


def test_provider_returns_chapter_dir_reflects_evolved_persona(tmp_path: Path) -> None:
    persona_dir = _make_base_persona(tmp_path)
    # 手写一个 chapter-2 演化人格目录
    ch2 = chapter_persona_dir(tmp_path, 2)
    ch2.mkdir(parents=True, exist_ok=True)
    (ch2 / "identity.md").write_text("我是一名校长。", encoding="utf-8")
    (ch2 / "style.md").write_text("沉稳、训诫式。", encoding="utf-8")
    state = load_growth_state(tmp_path / "growth-state.json")
    state.active_persona_chapter = 2
    save_growth_state(tmp_path / "growth-state.json", state)

    provider = make_active_core_provider(tmp_path / "growth-state.json", tmp_path)
    loader = PersonaLoader(persona_dir, active_core_provider=provider)

    prompt = loader.get().to_system_prompt()
    assert "校长" in prompt
    assert "三十六贱笑" not in prompt  # 激活的是 chapter-2，不是 base


def test_switching_active_chapter_changes_assembled_prompt(tmp_path: Path) -> None:
    persona_dir = _make_base_persona(tmp_path)
    # 写两章不同人格
    ch1 = chapter_persona_dir(tmp_path, 1)
    ch1.mkdir(parents=True, exist_ok=True)
    (ch1 / "identity.md").write_text("我是个脱口秀新人。", encoding="utf-8")
    ch2 = chapter_persona_dir(tmp_path, 2)
    ch2.mkdir(parents=True, exist_ok=True)
    (ch2 / "identity.md").write_text("我是一名校长。", encoding="utf-8")

    state_path = tmp_path / "growth-state.json"
    state = load_growth_state(state_path)
    provider = make_active_core_provider(state_path, tmp_path)
    loader = PersonaLoader(persona_dir, active_core_provider=provider)

    # 激活第 1 章
    state.active_persona_chapter = 1
    save_growth_state(state_path, state)
    loader.invalidate()
    assert "脱口秀新人" in loader.get().to_system_prompt()

    # 切到第 2 章 → 装配 prompt 随之变化
    state.active_persona_chapter = 2
    save_growth_state(state_path, state)
    loader.invalidate()
    p2 = loader.get().to_system_prompt()
    assert "校长" in p2
    assert "脱口秀新人" not in p2


# ── 端到端：成长 run 写人格 + base 零写 + 热生效 ──────────────────────────


@pytest.mark.asyncio
async def test_growth_run_writes_chapter_persona_and_never_touches_base(
    tmp_path: Path,
) -> None:
    persona_dir = _make_base_persona(tmp_path)
    base_before = _read_base_core(persona_dir)

    state_path = tmp_path / "growth-state.json"
    provider = make_active_core_provider(state_path, tmp_path)
    loader = PersonaLoader(persona_dir, active_core_provider=provider)
    engine = FakeEngine(
        _payload({"identity": "我是一名校长。", "style": "沉稳训诫。"})
    )
    runner = _make_runner(engine, tmp_path, persona_dir, loader)

    await runner({})

    # 状态推进 + 激活指针到第 1 章
    state = load_growth_state(state_path)
    assert state.current_chapter == 1
    assert state.active_persona_chapter == 1

    # chapter-0 起点快照 = base 五份；chapter-1 含演化的 identity + 承接的其余段落
    ch0 = chapter_persona_dir(tmp_path, 0)
    assert {p.name for p in ch0.glob("*.md")} == set(_BASE_CORE)
    ch1 = chapter_persona_dir(tmp_path, 1)
    assert "校长" in (ch1 / "identity.md").read_text(encoding="utf-8")
    # 没演化的 personality 承接自 chapter-0（= base 起点）
    assert "贫嘴" in (ch1 / "personality.md").read_text(encoding="utf-8")

    # loader 已 invalidate → get() 反映演化人格（校长），不再是 base
    prompt = loader.get().to_system_prompt()
    assert "校长" in prompt

    # **base persona/core 全程零写**（内容逐字未变）
    assert _read_base_core(persona_dir) == base_before


@pytest.mark.asyncio
async def test_partial_persona_carries_forward_prior_sections(tmp_path: Path) -> None:
    persona_dir = _make_base_persona(tmp_path)
    state_path = tmp_path / "growth-state.json"
    provider = make_active_core_provider(state_path, tmp_path)
    loader = PersonaLoader(persona_dir, active_core_provider=provider)

    # 第 1 章：演化 identity + beliefs
    engine1 = FakeEngine(
        _payload(
            {"identity": "我是脱口秀演员。", "beliefs": "让世界多笑一点。"},
            narrative="5-10 岁迷上逗笑",
        )
    )
    runner1 = _make_runner(engine1, tmp_path, persona_dir, loader)
    await runner1({})

    # 第 2 章：只演化 identity，其余段落必须承接第 1 章
    engine2 = FakeEngine(
        _payload({"identity": "我是一名校长。"}, narrative="10-15 岁转行教育")
    )
    runner2 = _make_runner(engine2, tmp_path, persona_dir, loader)
    await runner2({})

    state = load_growth_state(state_path)
    assert state.current_chapter == 2
    assert state.active_persona_chapter == 2

    ch2 = chapter_persona_dir(tmp_path, 2)
    # identity 是第 2 章新写的
    assert "校长" in (ch2 / "identity.md").read_text(encoding="utf-8")
    # beliefs 第 2 章没给 → 承接第 1 章演化结果（不是 base、也不为空）
    assert "让世界多笑一点" in (ch2 / "beliefs.md").read_text(encoding="utf-8")
    # personality 两章都没演化 → 一路承接 base 起点
    assert "贫嘴" in (ch2 / "personality.md").read_text(encoding="utf-8")

    # 装配 prompt 同时含第 2 章 identity + 承接的 beliefs
    prompt = loader.get().to_system_prompt()
    assert "校长" in prompt
    assert "让世界多笑一点" in prompt


@pytest.mark.asyncio
async def test_growth_run_without_persona_args_still_advances(tmp_path: Path) -> None:
    """不传 persona_dir/data_dir/loader（PR1 调用点）→ 跳过人格演化，仍写传记 + 推进状态。"""
    runner = GrowthRunner(
        engine=FakeEngine(_payload({"identity": "校长"})),  # type: ignore[arg-type]
        growth_state_path=tmp_path / "growth-state.json",
        memdir_dir=tmp_path / "memdir",
        start_age=5,
        years_per_chapter=5,
        end_age=30,
    )
    await runner({})

    state = load_growth_state(tmp_path / "growth-state.json")
    assert state.current_chapter == 1
    # 没传 data_dir → 不写任何 growth/persona 目录
    assert not (tmp_path / "growth" / "persona").exists()
