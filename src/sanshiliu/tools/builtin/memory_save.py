"""SaveMemory：LLM 主动写一条新记忆到 memdir。

设计取舍：
- 默认 allow（ADR）：与 auto-extract 同语义的"主动版"；不走 PermissionManager.check；
  用户若要禁用通过 settings.json deny 显式拒绝；
- name 校验：只允许字母/数字/短横线/下划线，长度 5-40；
- type 必须在 4 类（user/feedback/project/reference）内。
"""

from __future__ import annotations

import re
from typing import Any, cast

from sanshiliu.foundation.logging import get_logger
from sanshiliu.memory.longterm.memdir import MemdirLoader, write_memory_file
from sanshiliu.memory.types import (
    MEMORY_APPLIES,
    MEMORY_TYPES,
    MemoryEntry,
    MemoryType,
)
from sanshiliu.tools.types import FunctionTool, ToolDef, ToolResult

_logger = get_logger(__name__)

_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_NAME_MIN = 5
_NAME_MAX = 40


def _validate(name: str, mtype: str) -> str | None:
    if mtype not in MEMORY_TYPES:
        return f"type 非法：{mtype}；必须是 {'/'.join(MEMORY_TYPES)} 之一"
    if len(name) < _NAME_MIN or len(name) > _NAME_MAX:
        return f"name 长度必须 {_NAME_MIN}-{_NAME_MAX}，当前 {len(name)}"
    if not _NAME_RE.match(name):
        return "name 只允许字母/数字/短横线/下划线（不可含中文或特殊字符）"
    return None


def build_save_memory_tool(
    definition: ToolDef, memdir_loader: MemdirLoader,
) -> FunctionTool:
    async def _run(args: dict[str, Any]) -> ToolResult:
        name = str(args.get("name") or "").strip()
        mtype = str(args.get("type") or "").strip()
        description = str(args.get("description") or "").strip()
        body = str(args.get("body") or "").strip()
        apply = str(args.get("apply") or "").strip().lower() or None
        if not name or not mtype or not description or not body:
            return ToolResult(
                "", definition.name,
                "参数 name / type / description / body 都不能为空",
                is_error=True,
            )
        err = _validate(name, mtype)
        if err is not None:
            return ToolResult("", definition.name, err, is_error=True)
        if apply is not None and apply not in MEMORY_APPLIES:
            return ToolResult(
                "", definition.name,
                f"apply 非法：{apply}；目前只支持 {'/'.join(MEMORY_APPLIES)}",
                is_error=True,
            )
        memory_type = cast(MemoryType, mtype)
        # apply 已被上面的 `not in MEMORY_APPLIES` 守卫收窄，无需 cast（mtype 校验在 _validate 内、收窄不外传，仍需 cast）
        memory_apply = apply if apply is not None else None

        confidence_raw = args.get("confidence")
        confidence: float | None = None
        if confidence_raw is not None:
            try:
                confidence = float(confidence_raw)
            except (TypeError, ValueError):
                return ToolResult(
                    "", definition.name,
                    f"confidence 必须是数字：{confidence_raw}", is_error=True,
                )

        entry = MemoryEntry(
            name=name,
            description=description,
            memory_type=memory_type,
            body=body,
            confidence=confidence,
            apply=memory_apply,
        )
        try:
            file_path = write_memory_file(memdir_loader.root, entry, body)
        except OSError as exc:
            return ToolResult(
                "", definition.name, f"写入失败：{exc}", is_error=True,
            )
        memdir_loader.invalidate()
        _logger.info(
            "SaveMemory 已落盘",
            name=name, type=mtype, file=file_path.name,
        )
        return ToolResult(
            "", definition.name,
            f"已保存记忆: {name} (type={mtype}, file={file_path.name})",
        )

    return FunctionTool(_def=definition, _fn=_run)
