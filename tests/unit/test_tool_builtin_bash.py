"""bash_exec 单测；超时杀进程 + 输出截断 + exit_code 反馈。"""

from __future__ import annotations

import sys

import pytest

from sanshiliu.tools.builtin import build_bash_exec_tool
from sanshiliu.tools.types import ToolDef


def _def() -> ToolDef:
    return ToolDef(
        name="bash_exec", description="d",
        input_schema={"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
    )


async def test_bash_simple_success() -> None:
    tool = build_bash_exec_tool(_def())
    # cross-platform: echo 在 cmd 和 sh 都有
    res = await tool.execute({"command": "echo hello"})
    assert res.is_error is False
    assert "hello" in res.content
    assert "exit_code: 0" in res.content


async def test_bash_nonzero_exit_marked_error() -> None:
    tool = build_bash_exec_tool(_def())
    if sys.platform == "win32":
        cmd = "exit /b 7"
    else:
        cmd = "exit 7"
    res = await tool.execute({"command": cmd})
    assert res.is_error
    assert "exit_code: 7" in res.content


@pytest.mark.slow
async def test_bash_timeout_kills_process() -> None:
    """V-4：超时 → 杀进程 + 返超时。"""
    tool = build_bash_exec_tool(_def())
    if sys.platform == "win32":
        # Windows 用 timeout 命令实测会卡 stdin；用 ping 模拟 sleep
        cmd = "ping -n 30 127.0.0.1"
    else:
        cmd = "sleep 30"
    res = await tool.execute({"command": cmd, "timeout_sec": 1})
    assert res.is_error
    assert "超时" in res.content


async def test_bash_empty_command_rejected() -> None:
    tool = build_bash_exec_tool(_def())
    res = await tool.execute({"command": ""})
    assert res.is_error
    assert "不能为空" in res.content
