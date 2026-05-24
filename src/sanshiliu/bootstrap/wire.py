"""一键装配；把 Phase 1-8 所有子系统串成一个 App 对象。

REPL 和 web/wechat 通道复用同一份 App，只是 confirmer 不一样。
"""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from sanshiliu.bootstrap.banner import StatusSummary, summary_from_paths
from sanshiliu.context.manager import ContextManager
from sanshiliu.context.prompts import load_compact_prompts
from sanshiliu.engine.loop import ConversationEngine
from sanshiliu.foundation.config import Settings
from sanshiliu.foundation.errors import ConfigError
from sanshiliu.foundation.logging import get_logger
from sanshiliu.identity.loader import PersonaLoader
from sanshiliu.identity.watcher import PersonaWatcher
from sanshiliu.llm.client import LLMClient
from sanshiliu.memory.longterm.claudemd import ClaudeMdLoader
from sanshiliu.memory.longterm.extract import MemoryExtractor, load_extract_instruction
from sanshiliu.memory.longterm.memdir import MemdirLoader
from sanshiliu.memory.shortterm import ShortTermMemory
from sanshiliu.security.path_guard import PathGuard
from sanshiliu.security.permission import PermissionManager
from sanshiliu.security.settings_loader import SettingsLoader
from sanshiliu.security.types import Confirmer
from sanshiliu.skills.activator import SkillActivator
from sanshiliu.skills.loader import SkillLoader
from sanshiliu.storage.db import Database, get_database
from sanshiliu.tools.bootstrap import build_tool_stack
from sanshiliu.tools.dispatcher import ToolDispatcher
from sanshiliu.tools.registry import ToolRegistry

_logger = get_logger(__name__)


@dataclass
class App:
    """所有子系统的 facade；shutdown() 负责按相反顺序回收。"""

    settings: Settings
    db: Database
    llm: LLMClient
    persona_loader: PersonaLoader
    persona_watcher: PersonaWatcher
    context_manager: ContextManager
    engine: ConversationEngine
    summary: StatusSummary
    tool_registry: ToolRegistry | None = None
    tool_dispatcher: ToolDispatcher | None = None
    skill_loader: SkillLoader | None = None
    skill_activator: SkillActivator | None = None
    claudemd_loader: ClaudeMdLoader | None = None
    memdir_loader: MemdirLoader | None = None
    memory_extractor: MemoryExtractor | None = None
    short_term: ShortTermMemory | None = None
    settings_loader: SettingsLoader | None = None
    permission_manager: PermissionManager | None = None

    async def shutdown(self) -> None:
        """优雅回收；任意阶段异常不阻塞其余清理。"""
        try:
            await self.persona_watcher.stop()
        except Exception as exc:
            _logger.warning("persona_watcher.stop 异常", error=str(exc))
        try:
            await self.llm.close()
        except Exception as exc:
            _logger.warning("llm.close 异常", error=str(exc))
        try:
            await self.db.close()
        except Exception as exc:
            _logger.warning("db.close 异常", error=str(exc))


async def build_app(
    settings: Settings,
    *,
    cwd: Path | None = None,
    confirmer: Confirmer | None = None,
) -> App:
    """一份完整可运行的 App；confirmer 由通道传入（REPL 给 ReplConfirmer）。"""
    cwd_root = (cwd or Path.cwd()).resolve()

    # L3 身份
    persona_loader = PersonaLoader(settings.persona_dir)
    persona_loader.load()
    persona_watcher = PersonaWatcher(persona_loader)

    # L0 storage
    db = await get_database(settings.data_dir / "sanshiliu.db")

    # L2 LLM
    llm = LLMClient(
        api_key=settings.openai_api_key.get_secret_value(),
        base_url=settings.openai_base_url,
        model=settings.openai_model,
        db=db,
    )

    # L4 上下文
    compact_prompts = load_compact_prompts(settings.prompts_dir)
    context_manager = ContextManager(
        llm=llm, prompts=compact_prompts,
        max_context_tokens=settings.max_context_tokens,
        compact_threshold_ratio=settings.compact_threshold_ratio,
    )

    # L8 权限（先建，后给工具）
    settings_loader: SettingsLoader | None = None
    permission_manager: PermissionManager | None = None
    if settings.security_enabled:
        try:
            settings_loader = SettingsLoader(
                global_home=settings.home_dir, project_cwd=cwd_root,
            )
            settings_loader.load()
            permission_manager = PermissionManager(
                settings_loader=settings_loader,
                path_guard=PathGuard(cwd_root=cwd_root),
                confirmer=confirmer,
                db=db,
            )
        except Exception as exc:
            _logger.warning("权限管理装配失败（继续不带审批）", error=str(exc))

    # L6 skills（先于 L7 工具构造，工具栈把 Skill 暴露给 LLM 时需要 activator）
    skill_loader: SkillLoader | None = None
    skill_activator: SkillActivator | None = None
    if settings.skills_enabled:
        try:
            skill_loader = SkillLoader(
                [settings.skills_dir_project, settings.skills_dir_repo],
            )
            skills = skill_loader.load()
            if skills:
                skill_activator = SkillActivator(skill_loader)
        except Exception as exc:
            _logger.warning("skills 加载失败（继续不带 skills）", error=str(exc))

    # L7 工具
    tool_registry: ToolRegistry | None = None
    tool_dispatcher: ToolDispatcher | None = None
    if settings.tools_enabled:
        try:
            tool_registry, tool_dispatcher = build_tool_stack(
                prompts_dir=settings.prompts_dir, cwd_root=cwd_root,
                tavily_api_key=(
                    settings.tavily_api_key.get_secret_value()
                    if settings.tavily_api_key else None
                ),
                permission=permission_manager,
                skill_activator=skill_activator,
                db=db,
            )
        except ConfigError as exc:
            _logger.warning("工具栈加载失败（继续不带工具）", error=str(exc))

    # L5 长期记忆
    claudemd_loader: ClaudeMdLoader | None = None
    memdir_loader: MemdirLoader | None = None
    memory_extractor: MemoryExtractor | None = None
    short_term: ShortTermMemory | None = None
    if settings.memory_enabled:
        try:
            claudemd_loader = ClaudeMdLoader(
                global_home=settings.home_dir, project_cwd=cwd_root,
            )
            claudemd_loader.load()
            memdir_loader = MemdirLoader(settings.memdir_dir)
            memdir_loader.load()
            short_term = ShortTermMemory(settings.data_dir / "shortterm")
        except Exception as exc:
            _logger.warning("memory 加载失败（继续不带 memory）", error=str(exc))
            claudemd_loader = None
            memdir_loader = None

        if settings.auto_extract_enabled and memdir_loader is not None:
            try:
                instruction = load_extract_instruction(settings.prompts_dir)
                memory_extractor = MemoryExtractor(
                    llm=llm, memdir_root=memdir_loader.root, instruction=instruction,
                )
            except ConfigError as exc:
                _logger.warning("auto-extract 加载失败", error=str(exc))

    # L2 engine
    engine = ConversationEngine(
        llm=llm, db=db,
        persona_loader=persona_loader,
        context_manager=context_manager,
        tool_registry=tool_registry, tool_dispatcher=tool_dispatcher,
        skill_activator=skill_activator,
        claudemd_loader=claudemd_loader,
        memdir_loader=memdir_loader,
        memory_extractor=memory_extractor,
    )

    # 横幅状态汇总
    psnap = persona_loader.get()
    cmd_snap = claudemd_loader.get() if claudemd_loader is not None else None
    mem_snap = memdir_loader.get() if memdir_loader is not None else None
    perm = settings_loader.get() if settings_loader is not None else None
    channels: list[str] = ["repl"]
    if settings.web_enabled:
        channels.append("web")
    if settings.wechat_enabled:
        channels.append("wechat")

    summary = summary_from_paths(
        model=settings.openai_model,
        base_url=settings.openai_base_url,
        persona_dir=settings.persona_dir,
        persona_chars=psnap.total_chars(),
        skills_count=len(skill_loader.list()) if skill_loader is not None else 0,
        memory_chars=cmd_snap.total_chars() if cmd_snap is not None else 0,
        memory_entries=len(mem_snap.entries) if mem_snap is not None else 0,
        channels=tuple(channels),
        cwd=cwd_root,
        permission_mode=perm.default_mode if perm is not None else "disabled",
        permission_allow=len(perm.allow) if perm is not None else 0,
        permission_deny=len(perm.deny) if perm is not None else 0,
    )

    return App(
        settings=settings,
        db=db, llm=llm,
        persona_loader=persona_loader, persona_watcher=persona_watcher,
        context_manager=context_manager,
        engine=engine,
        tool_registry=tool_registry, tool_dispatcher=tool_dispatcher,
        skill_loader=skill_loader, skill_activator=skill_activator,
        claudemd_loader=claudemd_loader,
        memdir_loader=memdir_loader,
        memory_extractor=memory_extractor,
        short_term=short_term,
        settings_loader=settings_loader,
        permission_manager=permission_manager,
        summary=summary,
    )


# 类型别名：测试用得到
BuildAppFn = Callable[[Settings], Awaitable[App]]


def fail_fast_msg(exc: Exception) -> str:
    """把 wire 阶段异常转成对人友好的错误前缀；cli 用。"""
    return f"装配失败（{type(exc).__name__}）：{exc}"


def _ensure_imported() -> None:  # pragma: no cover
    """让 mypy 不抹掉 sys（pyflakes 容易误判）。"""
    _ = sys
