"""把"成长"包装成一个 HeartbeatTask（与心跳模块合并，满足 prd #3）。

闸门（gate）：读 growth-state.json，state.can_advance()（current_chapter < end_chapter）→ 放行；
              满 end_chapter 永久 false（30 岁定格）。无"同日不重复"限制——日级节奏由
              daily_at_hour 保证，手动 run_now 能连推几章便于快速验证 5 章。
触发（on_due）：调用 GrowthRunner 跑一章成长梦 + 写传记 + 推进状态。

注意：fire_hour 从 task.daily_at_hour 读、年龄/章数等从 state 文件读——dashboard PUT /config
改 extra_params 后立刻生效，无需重启（box[0] 闭包技巧，照搬 dream.py）。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from sanshiliu.scheduler.growth_runner import GrowthRunner
from sanshiliu.scheduler.growth_state import load_growth_state
from sanshiliu.scheduler.heartbeat import GateResult, HeartbeatTask

if TYPE_CHECKING:
    from sanshiliu.engine.loop import ConversationEngine
    from sanshiliu.identity.loader import PersonaLoader
    from sanshiliu.security.permission import PermissionManager
    from sanshiliu.skills.loader import SkillLoader
    from sanshiliu.storage.db import Database

# 默认值——首次启动 seed；后续被 heartbeat.json / growth-state.json 覆盖
_DEFAULT_FIRE_HOUR = 3
_DEFAULT_START_AGE = 5
_DEFAULT_YEARS_PER_CHAPTER = 5
_DEFAULT_END_AGE = 30


def build_growth_task(
    *,
    engine: ConversationEngine,
    db: Database | None,  # 透传给 GrowthRunner：直连 phase-2 自记 tool_calls 审计（#3）
    growth_state_path: Path,
    memdir_dir: Path,
    fire_hour: int = _DEFAULT_FIRE_HOUR,
    enabled: bool = False,
    start_age: int = _DEFAULT_START_AGE,
    years_per_chapter: int = _DEFAULT_YEARS_PER_CHAPTER,
    end_age: int = _DEFAULT_END_AGE,
    persona_dir: Path | None = None,
    data_dir: Path | None = None,
    persona_loader: PersonaLoader | None = None,
    skill_loader: SkillLoader | None = None,
    skill_install_timeout_sec: int = 60,
    skills_dir_global: Path | None = None,
    permission_manager: PermissionManager | None = None,
) -> HeartbeatTask:
    # PR2 人格演化（三者齐全才开启）：persona_dir = base core 来源、data_dir = 版本化人格落盘根、
    # persona_loader = 写完热生效的那个 loader（与 serve 主链路同一实例，否则改了不生效）。
    # PR3 技能习得：skill_loader = serve 主链路同一个 SkillLoader 实例——成长跑完用它 invalidate+
    # reload 做"装前/装后目录 diff"，并让新装的 skill 立刻被后续对话看到（否则装了也不生效）。
    # 方案 A：skill_install_timeout_sec 透传给 phase-2 装 skill 的 bash 硬超时（防 npx 挂死）。
    # skills_dir_global：installer 真正的落点（= settings.skills_dir_global），透传给 phase-2 安装 prompt
    # 据实点名目录——否则 prompt 会和实际落点错位（旧 bug #2）。
    runner = GrowthRunner(
        engine=engine,
        growth_state_path=growth_state_path,
        memdir_dir=memdir_dir,
        start_age=start_age,
        years_per_chapter=years_per_chapter,
        end_age=end_age,
        persona_dir=persona_dir,
        data_dir=data_dir,
        persona_loader=persona_loader,
        skill_loader=skill_loader,
        skill_install_timeout_sec=skill_install_timeout_sec,
        skills_dir_global=skills_dir_global,
        permission_manager=permission_manager,
        db=db,
    )

    # box[0] 闭包技巧（同 dream.py）：task 在 return 前还没构造好，用 list 占位，
    # gate/on_due 闭包读 box[0]——dashboard 改配置后立刻生效。
    box: list[HeartbeatTask | None] = [None]

    def _load() -> Any:
        return load_growth_state(
            growth_state_path,
            start_age=start_age,
            years_per_chapter=years_per_chapter,
            end_age=end_age,
        )

    async def gate() -> GateResult:
        state = _load()
        if not state.can_advance():
            return False, f"已满 {state.end_chapter} 章（{end_age} 岁定格），永久冻结"
        next_no = state.current_chapter + 1
        return True, f"可推进第 {next_no}/{state.end_chapter} 章（{state.next_age_range()} 岁）"

    async def on_due(ctx: dict[str, Any]) -> None:
        await runner(ctx)

    task = HeartbeatTask(
        name="growth",
        description="数字分身每天做一次成长梦：承接前章传记续写本章经历，整体演化人格，满 30 岁定格。",
        on_due=on_due,
        enabled=enabled,
        daily_at_hour=fire_hour,
        gate=gate,
        # 没有可调业务参数（年龄/章数走 state 文件）；留空 dict 与 dream 结构一致。
        # TODO(PR4): 若需 dashboard 调"是否自动装 skill"等开关，在这里加 editable_params。
        extra_params={},
        editable_params={},
    )
    box[0] = task
    return task
