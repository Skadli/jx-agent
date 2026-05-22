"""file_read / file_write 工具；路径必须落在 cwd_root 之内。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sanshiliu.foundation.logging import get_logger
from sanshiliu.tools.types import FunctionTool, ToolDef, ToolResult

_logger = get_logger(__name__)

# 单次 file_read 输出上限；超过截断
_MAX_BYTES_PER_READ = 200_000


def _resolve_within(cwd_root: Path, raw: str) -> Path:
    """把用户给的相对路径解析到绝对路径；不在 cwd_root 子树内抛 ValueError。"""
    candidate = (cwd_root / raw).resolve()
    try:
        candidate.relative_to(cwd_root.resolve())
    except ValueError as exc:
        raise ValueError(f"路径 {raw} 在工作目录之外") from exc
    return candidate


def build_file_read_tool(definition: ToolDef, cwd_root: Path) -> FunctionTool:
    async def _run(args: dict[str, Any]) -> ToolResult:
        raw_path = str(args.get("path") or "")
        offset = int(args.get("offset") or 1)
        limit = int(args.get("limit") or 200)
        if not raw_path:
            return ToolResult("", definition.name, "参数 path 不能为空", is_error=True)
        try:
            path = _resolve_within(cwd_root, raw_path)
        except ValueError as exc:
            return ToolResult("", definition.name, str(exc), is_error=True)
        if not path.is_file():
            return ToolResult("", definition.name, f"文件不存在：{raw_path}", is_error=True)
        try:
            data = path.read_bytes()
        except OSError as exc:
            return ToolResult("", definition.name, f"读失败：{exc}", is_error=True)

        truncated = False
        if len(data) > _MAX_BYTES_PER_READ:
            data = data[:_MAX_BYTES_PER_READ]
            truncated = True

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult(
                "", definition.name, "文件不是 UTF-8 文本", is_error=True,
            )

        lines = text.splitlines()
        start = max(0, offset - 1)
        end = min(len(lines), start + limit)
        out = "\n".join(f"{i + 1:>4}\t{lines[i]}" for i in range(start, end))
        if truncated:
            out += "\n[... 字节数已截断 ...]"
        return ToolResult("", definition.name, out, truncated=truncated)

    return FunctionTool(_def=definition, _fn=_run)


def build_file_write_tool(definition: ToolDef, cwd_root: Path) -> FunctionTool:
    async def _run(args: dict[str, Any]) -> ToolResult:
        raw_path = str(args.get("path") or "")
        content = args.get("content")
        if not raw_path or content is None:
            return ToolResult(
                "", definition.name, "参数 path / content 都不能为空", is_error=True,
            )
        try:
            path = _resolve_within(cwd_root, raw_path)
        except ValueError as exc:
            return ToolResult("", definition.name, str(exc), is_error=True)
        # 拒绝以 . 开头的目录路径（隐藏目录）；但允许 .gitignore 这种顶层隐藏文件
        if any(part.startswith(".") and part not in {".", ".."} for part in path.parts[len(cwd_root.parts):-1]):
            return ToolResult(
                "", definition.name, f"拒绝写入隐藏目录路径：{raw_path}", is_error=True,
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(str(content), encoding="utf-8")
        except OSError as exc:
            return ToolResult("", definition.name, f"写失败：{exc}", is_error=True)
        return ToolResult(
            "", definition.name,
            f"已写入 {path.relative_to(cwd_root.resolve())}（{len(str(content))} 字符）",
        )

    return FunctionTool(_def=definition, _fn=_run)
