"""file_read / file_write 单测；含路径越界拦截。"""

from __future__ import annotations

from pathlib import Path

from sanshiliu.tools.builtin import build_file_read_tool, build_file_write_tool
from sanshiliu.tools.types import ToolDef


def _read_def() -> ToolDef:
    return ToolDef(
        name="file_read", description="d",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    )


def _write_def() -> ToolDef:
    return ToolDef(
        name="file_write", description="d",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
    )


async def test_file_read_basic(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")
    tool = build_file_read_tool(_read_def(), tmp_path)
    res = await tool.execute({"path": "a.txt"})
    assert res.is_error is False
    assert "line1" in res.content
    # 含行号前缀
    assert "   1\tline1" in res.content


async def test_file_read_offset_limit(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("\n".join(f"L{i}" for i in range(20)), encoding="utf-8")
    tool = build_file_read_tool(_read_def(), tmp_path)
    res = await tool.execute({"path": "a.txt", "offset": 5, "limit": 3})
    assert "L4" in res.content  # offset=5 → index 4
    assert "L6" in res.content
    assert "L7" not in res.content


async def test_file_read_path_escape_blocked(tmp_path: Path) -> None:
    """V 安全：路径越界拦截。"""
    tool = build_file_read_tool(_read_def(), tmp_path)
    res = await tool.execute({"path": "../outside.txt"})
    assert res.is_error
    assert "工作目录之外" in res.content


async def test_file_read_missing_file(tmp_path: Path) -> None:
    tool = build_file_read_tool(_read_def(), tmp_path)
    res = await tool.execute({"path": "nope.txt"})
    assert res.is_error
    assert "不存在" in res.content


async def test_file_write_creates_file(tmp_path: Path) -> None:
    tool = build_file_write_tool(_write_def(), tmp_path)
    res = await tool.execute({"path": "out.txt", "content": "hello"})
    assert res.is_error is False
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "hello"


async def test_file_write_path_escape_blocked(tmp_path: Path) -> None:
    tool = build_file_write_tool(_write_def(), tmp_path)
    res = await tool.execute({"path": "../bad.txt", "content": "x"})
    assert res.is_error
    assert "工作目录之外" in res.content


async def test_file_write_hidden_dir_blocked(tmp_path: Path) -> None:
    tool = build_file_write_tool(_write_def(), tmp_path)
    res = await tool.execute({"path": ".secret/x.txt", "content": "x"})
    assert res.is_error
    assert "隐藏" in res.content


async def test_file_write_creates_parent_dirs(tmp_path: Path) -> None:
    tool = build_file_write_tool(_write_def(), tmp_path)
    res = await tool.execute({"path": "sub/nested/file.txt", "content": "x"})
    assert res.is_error is False
    assert (tmp_path / "sub" / "nested" / "file.txt").exists()
