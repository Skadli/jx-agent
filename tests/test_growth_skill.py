"""PR3 成长技能习得单测：目录 diff 记账 + 无人值守自动放行窗口。

不打真 LLM、不碰真 git/网络：用一个会在 complete_turn 里"假装安装"的 FakeEngine 桩，
往一个真 SkillLoader 指向的临时 skills 目录写一个 <id>/SKILL.md 来模拟 skill-installer
落产物——runner 跑完按"装前/装后目录 diff"把它记进 installed_skills（目录是真相源）。
风格对齐 tests/test_growth_runner.py 与 tests/test_heartbeat_scheduler.py。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sanshiliu.engine.types import ChatMessage
from sanshiliu.scheduler.growth_runner import GrowthRunner
from sanshiliu.scheduler.growth_state import load_growth_state
from sanshiliu.security.composite_confirmer import CompositeConfirmer
from sanshiliu.security.growth_approvals import (
    GrowthAutoConfirmer,
    enter_growth_autoallow,
    exit_growth_autoallow,
    in_growth_autoallow,
)
from sanshiliu.security.permission import PermissionManager
from sanshiliu.security.settings_loader import SettingsLoader
from sanshiliu.skills.loader import SkillLoader

# 一份最小但合法的 SKILL.md（loader 要求 frontmatter 含 name + description）
_SKILL_MD = """---
name: 假装脱口秀
description: 测试用，模拟 skill-installer 装进来的真实 skill。
---

正文随便写点。
"""

# 合法成长结构化输出（含一条 skill_intent）；runner 据 narrative 落传记、据 diff 记 skill
_VALID_PAYLOAD = {
    "narrative": "5 到 10 岁，我从三十六贱笑的底色长成了爱写段子的小学生。",
    "age_range": "5-10",
    "learned": ["写打油诗"],
    "personality": "一个早慧又贫嘴的孩子。",
    "skill_intents": [{"domain": "脱口秀", "why": "这一章迷上了逗人笑"}],
}


def _write_skill(skills_dir: Path, skill_id: str) -> None:
    """模拟 installer 把一个真实 skill 落成 skills/<id>/SKILL.md。"""
    d = skills_dir / skill_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")


class InstallingEngine:
    """方案 A 两段：phase-1（use_tools=False）回传记 JSON；phase-2（use_tools=True）"装"一个 skill。

    顺带断言：装这一刻（phase-2）必须处在成长自动放行窗口内（contextvar 为真）——否则真实场景下
    installer 的工具调用会被 CompositeConfirmer 拒掉。phase-1 不在窗口内（它无工具）。
    """

    def __init__(self, skills_dir: Path, skill_id: str, reply_payload: dict[str, Any]) -> None:
        self._skills_dir = skills_dir
        self._skill_id = skill_id
        self._reply = reply_payload
        self.autoallow_during_install: bool | None = None
        self.autoallow_during_phase1: bool | None = None
        self.calls = 0

    async def complete_turn(
        self, _session: Any, _user_text: Any, *, max_turns: int | None = None,
        on_user_message: Any = None, use_tools: bool = True,
    ) -> ChatMessage:
        self.calls += 1
        if use_tools:
            # phase-2：装 skill 段——记录此刻自动放行是否生效（应为真）
            self.autoallow_during_install = in_growth_autoallow()
            _write_skill(self._skills_dir, self._skill_id)
            return ChatMessage(role="assistant", content="装好了")
        # phase-1：传记段（零工具，不在放行窗口内）
        self.autoallow_during_phase1 = in_growth_autoallow()
        return ChatMessage(
            role="assistant",
            content="```json\n" + json.dumps(self._reply, ensure_ascii=False) + "\n```",
        )


class NoInstallEngine:
    """phase-1 回结构化输出；phase-2 不装任何 skill——模拟"找不到真实 skill，当章不装"。"""

    def __init__(self, reply_payload: dict[str, Any]) -> None:
        self._reply = reply_payload
        self.calls = 0

    async def complete_turn(
        self, _session: Any, _user_text: Any, *, max_turns: int | None = None,
        on_user_message: Any = None, use_tools: bool = True,
    ) -> ChatMessage:
        self.calls += 1
        if use_tools:
            return ChatMessage(role="assistant", content="都没找到合适的，跳过了")
        return ChatMessage(
            role="assistant",
            content="```json\n" + json.dumps(self._reply, ensure_ascii=False) + "\n```",
        )


def _make_runner(engine: Any, tmp_path: Path, skills_dir: Path) -> GrowthRunner:
    loader = SkillLoader([skills_dir])  # 真 loader，指向临时 skills 目录
    loader.load()  # 建立装前基线缓存
    return GrowthRunner(
        engine=engine,  # type: ignore[arg-type]  测试桩，鸭子类型即可
        growth_state_path=tmp_path / "growth-state.json",
        memdir_dir=tmp_path / "memdir",
        start_age=5,
        years_per_chapter=5,
        end_age=30,
        skill_loader=loader,
    )


@pytest.mark.asyncio
async def test_installed_skill_recorded_via_dir_diff(tmp_path: Path) -> None:
    # (a) phase-2 装上一个 skill（写目录）→ runner 按目录 diff 回填进本章 installed_skills
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    engine = InstallingEngine(skills_dir, "talkshow", _VALID_PAYLOAD)
    runner = _make_runner(engine, tmp_path, skills_dir)

    await runner({})

    state = load_growth_state(tmp_path / "growth-state.json")
    assert state.current_chapter == 1
    # 目录 diff 把新增的 talkshow 记进本章 installed_skills（不是靠 LLM 自报）
    assert state.chapters[0].installed_skills == ["talkshow"]


@pytest.mark.asyncio
async def test_no_install_yields_empty_list_no_error(tmp_path: Path) -> None:
    # (b) 找不到真实 skill → 当章不装 → installed_skills 为空、不报错、照常推进
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    engine = NoInstallEngine(_VALID_PAYLOAD)
    runner = _make_runner(engine, tmp_path, skills_dir)

    await runner({})

    state = load_growth_state(tmp_path / "growth-state.json")
    assert state.current_chapter == 1  # 没装 skill 不影响传记 + 状态推进
    assert state.chapters[0].installed_skills == []


@pytest.mark.asyncio
async def test_autoallow_set_during_phase2_only_and_reset_after(tmp_path: Path) -> None:
    # (c) 自动放行窗口只圈 phase-2：装那一刻 contextvar 为真、phase-1 为假；跑完复位为假（不外溢）
    assert in_growth_autoallow() is False  # 跑之前就不该是放行状态
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    engine = InstallingEngine(skills_dir, "talkshow", _VALID_PAYLOAD)
    runner = _make_runner(engine, tmp_path, skills_dir)

    await runner({})

    assert engine.autoallow_during_phase1 is False  # 传记段（phase-1）不在放行窗口内
    assert engine.autoallow_during_install is True  # 装那一刻（phase-2）确实在放行窗口内
    assert in_growth_autoallow() is False  # 窗口已复位，不污染后续请求


@pytest.mark.asyncio
async def test_autoallow_reset_even_when_phase2_raises(tmp_path: Path) -> None:
    # 放行窗口必须在 phase-2 异常路径也复位（finally）——否则放行权限会泄漏到别的请求。
    # 方案 A：phase-2 炸**不上抛**（章已成立），但 contextvar 仍须复位、状态仍推进。
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    class Phase2BoomEngine:
        calls = 0

        async def complete_turn(
            self, _session: Any, _user_text: Any, *, max_turns: int | None = None,
            on_user_message: Any = None, use_tools: bool = True,
        ) -> ChatMessage:
            Phase2BoomEngine.calls += 1
            if use_tools:
                assert in_growth_autoallow() is True  # phase-2 进了窗口
                raise RuntimeError("phase-2 装 skill 炸了")
            return ChatMessage(  # phase-1 正常出 JSON
                role="assistant",
                content="```json\n" + json.dumps(_VALID_PAYLOAD, ensure_ascii=False) + "\n```",
            )

    runner = _make_runner(Phase2BoomEngine(), tmp_path, skills_dir)
    await runner({})  # phase-2 炸了也不上抛——章照常成立

    assert in_growth_autoallow() is False  # 异常路径也复位（finally 必复位）
    state = load_growth_state(tmp_path / "growth-state.json")
    assert state.current_chapter == 1  # phase-1 已推进，phase-2 炸不回退


@pytest.mark.asyncio
async def test_skill_loader_absent_records_empty_no_error(tmp_path: Path) -> None:
    # 不传 skill_loader（如别的调用点/早期接线）→ installed_skills 空、整章照常跑、不报错
    runner = GrowthRunner(
        engine=NoInstallEngine(_VALID_PAYLOAD),  # type: ignore[arg-type]
        growth_state_path=tmp_path / "growth-state.json",
        memdir_dir=tmp_path / "memdir",
        start_age=5,
        years_per_chapter=5,
        end_age=30,
    )

    await runner({})

    state = load_growth_state(tmp_path / "growth-state.json")
    assert state.current_chapter == 1
    assert state.chapters[0].installed_skills == []


# ── 安全边界（#5 的最关键不变量）：成长自动放行**绝不能**越过 settings.deny / critical 硬底线 ──
# 这是真正的供应链/注入防线——自动放行只作用于"会询问的那批"，deny 与 critical 在
# PermissionManager.check 里的 ask 之前就返回，GrowthAutoConfirmer 根本接触不到。


def _make_permission_manager(tmp_path: Path, settings_obj: dict[str, Any]) -> PermissionManager:
    """造一个真 PermissionManager，confirmer 装上成长自动放行；settings 指向临时目录。"""
    (tmp_path / "settings.json").write_text(
        json.dumps(settings_obj, ensure_ascii=False), encoding="utf-8"
    )
    loader = SettingsLoader(global_home=tmp_path, project_cwd=tmp_path)
    loader.load()
    return PermissionManager(
        settings_loader=loader,
        confirmer=CompositeConfirmer(growth=GrowthAutoConfirmer()),
        db=None,  # _persist_decision 在 db=None 时直接 return，不落库
    )


@pytest.mark.asyncio
async def test_growth_autoallow_passes_ask_path_tool(tmp_path: Path) -> None:
    # 正向：defaultMode=ask 下，会询问的工具（Skill 调用）在成长窗口内被自动放行
    pm = _make_permission_manager(tmp_path, {"permissions": {"defaultMode": "ask"}})
    token = enter_growth_autoallow()
    try:
        decision = await pm.check(
            tool_name="Skill", arguments={"skill": "skill-finder"}, session_id="growth-1"
        )
    finally:
        exit_growth_autoallow(token)
    assert decision.kind == "allow"
    assert decision.source == "user-confirmed"  # 走的是 ask→confirmer→GrowthAutoConfirmer


@pytest.mark.asyncio
async def test_growth_autoallow_cannot_override_settings_deny(tmp_path: Path) -> None:
    # 边界 1：settings.deny 命中的工具，即使在成长自动放行窗口内也必须被拒
    pm = _make_permission_manager(
        tmp_path,
        {"permissions": {"defaultMode": "ask", "deny": ["Skill"]}},
    )
    token = enter_growth_autoallow()
    try:
        decision = await pm.check(
            tool_name="Skill", arguments={"skill": "skill-finder"}, session_id="growth-1"
        )
    finally:
        exit_growth_autoallow(token)
    assert decision.kind == "deny"
    assert decision.source == "settings-deny"  # deny 在 ask 之前返回，confirmer 没被调用


@pytest.mark.asyncio
async def test_growth_autoallow_cannot_override_critical_bash(tmp_path: Path) -> None:
    # 边界 2：critical 档 bash（rm -rf /）即使在成长自动放行窗口内也必须硬拒
    pm = _make_permission_manager(tmp_path, {"permissions": {"defaultMode": "ask"}})
    token = enter_growth_autoallow()
    try:
        decision = await pm.check(
            tool_name="Bash", arguments={"command": "rm -rf /"}, session_id="growth-1"
        )
    finally:
        exit_growth_autoallow(token)
    assert decision.kind == "deny"
    assert decision.source == "critical-hard-deny"  # critical 硬底线在 ask 之前返回


@pytest.mark.asyncio
async def test_growth_autoallow_compound_critical_still_denied(tmp_path: Path) -> None:
    # 边界 2 加强：safe 前缀 + critical 后段的复合命令也必须被识别为 critical 并硬拒
    pm = _make_permission_manager(tmp_path, {"permissions": {"defaultMode": "ask"}})
    token = enter_growth_autoallow()
    try:
        decision = await pm.check(
            tool_name="Bash",
            arguments={"command": "git status && rm -rf /"},
            session_id="growth-1",
        )
    finally:
        exit_growth_autoallow(token)
    assert decision.kind == "deny"
    assert decision.source == "critical-hard-deny"
