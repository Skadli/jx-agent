"""工具注册表；按名查找 + 导出 OpenAI tools 数组。"""

from __future__ import annotations

from pathlib import Path

from sanshiliu.foundation.errors import ConfigError
from sanshiliu.foundation.frontmatter import parse
from sanshiliu.foundation.logging import get_logger
from sanshiliu.tools.types import Tool, ToolDef

_logger = get_logger(__name__)


class ToolRegistry:
    """单实例；启动期注册 builtin tool 后只读。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        name = tool.definition.name
        if name in self._tools:
            raise ConfigError(f"工具名重复注册：{name}")
        self._tools[name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    def definitions(self) -> list[ToolDef]:
        return [t.definition for t in self._tools.values()]

    def to_openai_tools(self) -> list[dict]:
        return [d.to_openai() for d in self.definitions()]

    @property
    def is_empty(self) -> bool:
        return not self._tools


def load_tool_definitions(prompts_tools_dir: Path) -> dict[str, ToolDef]:
    """从 prompts/tools/*.md 读 frontmatter 拼装 ToolDef；name/description/parameters 必填。"""
    if not prompts_tools_dir.is_dir():
        raise ConfigError(
            f"tool 描述目录不存在：{prompts_tools_dir}\n"
            "  解决：建立 prompts/tools/ 并放入 *.md，或设 SANSHILIU_PROMPTS_DIR",
        )

    out: dict[str, ToolDef] = {}
    for path in sorted(prompts_tools_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        parsed = parse(text)
        fm = parsed.frontmatter
        missing = [k for k in ("name", "description", "parameters") if k not in fm]
        if missing:
            raise ConfigError(
                f"工具描述 {path.name} 缺字段：{', '.join(missing)}",
            )
        name = str(fm["name"])
        description = str(fm["description"])
        params = fm["parameters"]
        if not isinstance(params, dict):
            raise ConfigError(f"工具描述 {path.name} parameters 必须是 dict")
        out[name] = ToolDef(
            name=name,
            description=description,
            input_schema=params,
        )
        _logger.info("工具定义已加载", name=name, file=path.name)
    return out
