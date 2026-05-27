"""LoadMemory：按 name 读单条 memdir 记忆的 frontmatter + body 给 LLM。

设计取舍：
- 不走 PathGuard：memdir 目录是 agent 自治领域，不是用户 cwd 的一部分；
- 不走 PermissionManager：纯读操作，与 web_search/file_read（path_guard clean）同级，
  自动放行；
- 找不到时返 is_error，附前 10 个 name 提示 LLM 选择正确条目。
"""

from __future__ import annotations

from typing import Any

from sanshiliu.foundation.logging import get_logger
from sanshiliu.memory.longterm.memdir import MemdirLoader
from sanshiliu.memory.types import MemoryEntry
from sanshiliu.tools.types import FunctionTool, ToolDef, ToolResult

_logger = get_logger(__name__)


def _render(entry: MemoryEntry) -> str:
    """frontmatter（仅常用字段）+ \\n\\n + body 拼回工具结果文本。"""
    lines = [
        "---",
        f"name: {entry.name}",
        f"type: {entry.memory_type}",
        f"description: {entry.description}",
    ]
    if entry.source:
        lines.append(f"source: {entry.source}")
    if entry.confidence is not None:
        lines.append(f"confidence: {entry.confidence}")
    if entry.protected:
        lines.append("protected: true")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + (entry.body or "").strip()


def build_load_memory_tool(
    definition: ToolDef, memdir_loader: MemdirLoader,
) -> FunctionTool:
    async def _run(args: dict[str, Any]) -> ToolResult:
        name = str(args.get("name") or "").strip()
        if not name:
            return ToolResult("", definition.name, "参数 name 不能为空", is_error=True)
        snap = memdir_loader.get()
        for entry in snap.entries:
            if entry.name == name:
                _logger.info("LoadMemory 命中", name=name, type=entry.memory_type)
                return ToolResult("", definition.name, _render(entry))
        # 未命中：列前 10 个可用 name 帮 LLM 纠正
        available = ", ".join(e.name for e in snap.entries[:10]) or "（无）"
        return ToolResult(
            "", definition.name,
            f"未找到记忆: {name}。可用条目: {available}",
            is_error=True,
        )

    return FunctionTool(_def=definition, _fn=_run)
