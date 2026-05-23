"""tools 装配工厂；外层一次性构造好 registry + dispatcher。"""

from __future__ import annotations

from pathlib import Path

from sanshiliu.foundation.logging import get_logger
from sanshiliu.security.permission import PermissionManager
from sanshiliu.tools.builtin import (
    build_bash_exec_tool,
    build_file_read_tool,
    build_file_write_tool,
    build_web_search_tool,
)
from sanshiliu.tools.dispatcher import ToolDispatcher
from sanshiliu.tools.registry import ToolRegistry, load_tool_definitions

_logger = get_logger(__name__)


def build_tool_stack(
    *,
    prompts_dir: Path,
    cwd_root: Path,
    tavily_api_key: str | None = None,
    permission: PermissionManager | None = None,
) -> tuple[ToolRegistry, ToolDispatcher]:
    """从 prompts/tools/ 加载描述 + 绑定内置 executor；缺描述文件抛 ConfigError。"""
    defs = load_tool_definitions(prompts_dir / "tools")
    registry = ToolRegistry()

    builders = {
        "web_search": lambda d: build_web_search_tool(d, tavily_api_key=tavily_api_key),
        "file_read": lambda d: build_file_read_tool(d, cwd_root),
        "file_write": lambda d: build_file_write_tool(d, cwd_root),
        "bash_exec": lambda d: build_bash_exec_tool(d, cwd=str(cwd_root)),
    }
    for name, definition in defs.items():
        if name not in builders:
            _logger.warning("跳过未知工具", name=name)
            continue
        registry.register(builders[name](definition))

    dispatcher = ToolDispatcher(registry, permission=permission)
    _logger.info("工具栈就绪", tools=registry.names(), permission=bool(permission))
    return registry, dispatcher
