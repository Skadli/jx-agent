"""tools 装配工厂；外层一次性构造好 registry + dispatcher。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from sanshiliu.foundation.logging import get_logger
from sanshiliu.identity.module_activator import PersonaModuleActivator
from sanshiliu.memory.longterm.memdir import MemdirLoader
from sanshiliu.memory.shortterm import ShortTermMemory
from sanshiliu.security.permission import PermissionManager
from sanshiliu.skills.activator import SkillActivator
from sanshiliu.storage.db import Database
from sanshiliu.tools.builtin import (
    build_bash_exec_tool,
    build_file_read_tool,
    build_file_write_tool,
    build_load_memory_tool,
    build_load_persona_module_tool,
    build_save_memory_tool,
    build_skill_tool,
    build_web_search_tool,
)
from sanshiliu.tools.dispatcher import ToolDispatcher
from sanshiliu.tools.registry import ToolRegistry, load_tool_definitions
from sanshiliu.tools.types import Tool

_logger = get_logger(__name__)


def build_tool_stack(
    *,
    prompts_dir: Path,
    cwd_root: Path,
    tavily_api_key: str | None = None,
    permission: PermissionManager | None = None,
    skill_activator: SkillActivator | None = None,
    persona_module_activator: PersonaModuleActivator | None = None,
    memdir_loader: MemdirLoader | None = None,
    short_term: ShortTermMemory | None = None,
    db: Database | None = None,
) -> tuple[ToolRegistry, ToolDispatcher]:
    """从 prompts/tools/ 加载描述 + 绑定内置 executor；缺描述文件抛 ConfigError。

    Skill / LoadPersonaModule / LoadMemory / SaveMemory 工具仅在对应 activator/loader
    非空时注册；prompts/tools/{skill,load_persona_module,memory_load,memory_save}.md
    缺失则跳过。
    """
    defs = load_tool_definitions(prompts_dir / "tools")
    registry = ToolRegistry()

    builders: dict[str, Callable[[Any], Tool]] = {
        "web_search": lambda d: build_web_search_tool(d, tavily_api_key=tavily_api_key),
        "file_read": lambda d: build_file_read_tool(d, cwd_root),
        "file_write": lambda d: build_file_write_tool(d, cwd_root),
        "bash_exec": lambda d: build_bash_exec_tool(d, cwd=str(cwd_root)),
    }
    if skill_activator is not None:
        builders["Skill"] = lambda d: build_skill_tool(d, skill_activator, db)
    if persona_module_activator is not None:
        builders["LoadPersonaModule"] = lambda d: build_load_persona_module_tool(
            d, persona_module_activator,
        )
    if memdir_loader is not None:
        builders["LoadMemory"] = lambda d: build_load_memory_tool(
            d, memdir_loader, short_term=short_term, db=db,
        )
        builders["SaveMemory"] = lambda d: build_save_memory_tool(d, memdir_loader)
    for name, definition in defs.items():
        if name not in builders:
            _logger.warning("跳过未知工具", name=name)
            continue
        registry.register(builders[name](definition))

    dispatcher = ToolDispatcher(registry, permission=permission)
    _logger.info("工具栈就绪", tools=registry.names(), permission=bool(permission))
    return registry, dispatcher
