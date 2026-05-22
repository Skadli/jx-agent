"""ToolRegistry + load_tool_definitions 单测。"""

from __future__ import annotations

from pathlib import Path

import pytest

from sanshiliu.foundation.errors import ConfigError
from sanshiliu.tools.registry import ToolRegistry, load_tool_definitions
from sanshiliu.tools.types import FunctionTool, ToolDef, ToolResult


def _seed_tools_md(dir_: Path) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "foo.md").write_text(
        "---\nname: foo\ndescription: do foo\nparameters:\n  type: object\n  properties:\n    x:\n      type: string\n  required: [x]\n---\nbody",
        encoding="utf-8",
    )
    (dir_ / "bar.md").write_text(
        "---\nname: bar\ndescription: do bar\nparameters:\n  type: object\n  properties: {}\n---\nbody",
        encoding="utf-8",
    )


def test_load_definitions(tmp_path: Path) -> None:
    _seed_tools_md(tmp_path)
    defs = load_tool_definitions(tmp_path)
    assert set(defs.keys()) == {"foo", "bar"}
    assert defs["foo"].input_schema["required"] == ["x"]


def test_load_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_tool_definitions(tmp_path / "no-such-dir")


def test_load_missing_field(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "x.md").write_text(
        "---\nname: x\n---\nno description",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_tool_definitions(tmp_path)
    assert "description" in str(exc_info.value)


def test_registry_register_and_get() -> None:
    r = ToolRegistry()
    d = ToolDef(name="t", description="d", input_schema={"type": "object"})

    async def _run(_: dict) -> ToolResult:
        return ToolResult("", "t", "ok")

    r.register(FunctionTool(d, _run))
    assert r.get("t") is not None
    assert r.names() == ["t"]
    assert r.is_empty is False


def test_registry_duplicate_raises() -> None:
    r = ToolRegistry()
    d = ToolDef(name="t", description="d", input_schema={})

    async def _run(_: dict) -> ToolResult:
        return ToolResult("", "t", "ok")

    r.register(FunctionTool(d, _run))
    with pytest.raises(ConfigError):
        r.register(FunctionTool(d, _run))


def test_registry_to_openai_format() -> None:
    r = ToolRegistry()
    d = ToolDef(name="t", description="d", input_schema={"type": "object", "properties": {}})

    async def _run(_: dict) -> ToolResult:
        return ToolResult("", "t", "ok")

    r.register(FunctionTool(d, _run))
    out = r.to_openai_tools()
    assert out == [{"type": "function", "function": {"name": "t", "description": "d", "parameters": {"type": "object", "properties": {}}}}]
