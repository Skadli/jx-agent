"""bash_exec：subprocess + 超时 + 输出截断；跨平台。"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from sanshiliu.foundation.logging import get_logger
from sanshiliu.tools.types import FunctionTool, ToolDef, ToolResult

_logger = get_logger(__name__)

# stdout/stderr 各自单独截断
_MAX_OUTPUT_CHARS = 4000


def build_bash_exec_tool(definition: ToolDef, cwd: str | None = None) -> FunctionTool:
    async def _run(args: dict[str, Any]) -> ToolResult:
        cmd = str(args.get("command") or "").strip()
        if not cmd:
            return ToolResult("", definition.name, "参数 command 不能为空", is_error=True)
        timeout = float(args.get("timeout_sec") or 30)
        # 用 shell=True 简化，但限制超时 + 截断输出
        if sys.platform == "win32":
            shell = ["cmd.exe", "/c", cmd]
        else:
            shell = ["/bin/sh", "-c", cmd]

        try:
            proc = await asyncio.create_subprocess_exec(
                *shell,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd or os.getcwd(),
            )
        except FileNotFoundError as exc:
            return ToolResult("", definition.name, f"shell 不可用：{exc}", is_error=True)

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:  # noqa: BLE001
                pass
            return ToolResult(
                "", definition.name,
                f"命令超时（{timeout}s），进程已杀；命令：{cmd[:200]}",
                is_error=True,
            )

        stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
        stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
        truncated = False
        if len(stdout) > _MAX_OUTPUT_CHARS:
            stdout = stdout[:_MAX_OUTPUT_CHARS] + "\n[... stdout 截断 ...]"
            truncated = True
        if len(stderr) > _MAX_OUTPUT_CHARS:
            stderr = stderr[:_MAX_OUTPUT_CHARS] + "\n[... stderr 截断 ...]"
            truncated = True

        rc = proc.returncode
        body = (
            f"$ {cmd}\n"
            f"exit_code: {rc}\n"
            f"--- stdout ---\n{stdout or '(空)'}\n"
            f"--- stderr ---\n{stderr or '(空)'}"
        )
        return ToolResult(
            "", definition.name, body, is_error=(rc != 0), truncated=truncated,
        )

    return FunctionTool(_def=definition, _fn=_run)
