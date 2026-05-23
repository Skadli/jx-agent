"""工具调用分发器；解析 LLM tool_call → 校验 → 权限 → 路由 → 包 ToolResult。"""

from __future__ import annotations

import json
from typing import Any

from sanshiliu.foundation.logging import get_logger
from sanshiliu.security.permission import PermissionManager
from sanshiliu.tools.registry import ToolRegistry
from sanshiliu.tools.types import ToolCall, ToolResult

_logger = get_logger(__name__)

# 单次 tool 输出截断阈值；超过返回 truncated 标记
_MAX_RESULT_CHARS = 8000


def parse_tool_calls(raw: list[dict[str, Any]]) -> list[ToolCall]:
    """OpenAI choices[0].message.tool_calls 列表 → ToolCall 列表；解析参数 JSON。"""
    out: list[ToolCall] = []
    for tc in raw:
        fn = tc.get("function", {})
        name = str(fn.get("name") or "")
        args_raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
        except json.JSONDecodeError as exc:
            _logger.warning("tool_call arguments 非合法 JSON，置为空 dict", error=str(exc))
            args = {}
        out.append(ToolCall(id=str(tc.get("id") or ""), name=name, arguments=args))
    return out


def _validate_required(args: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """极简 required 字段校验；不做完整 JSON Schema 验证。"""
    required = schema.get("required") or []
    return [k for k in required if k not in args]


class ToolDispatcher:
    """注册表 + 安全壳；每次 execute 把异常变成 ToolResult(is_error=True)。"""

    def __init__(
        self,
        registry: ToolRegistry,
        permission: PermissionManager | None = None,
    ) -> None:
        self._registry = registry
        self._permission = permission

    async def execute(self, call: ToolCall, *, session_id: str = "") -> ToolResult:
        tool = self._registry.get(call.name)
        if tool is None:
            return ToolResult(
                call_id=call.id, name=call.name,
                content=f"未知工具：{call.name}", is_error=True,
            )

        missing = _validate_required(call.arguments, tool.definition.input_schema)
        if missing:
            return ToolResult(
                call_id=call.id, name=call.name,
                content=f"参数缺失：{', '.join(missing)}", is_error=True,
            )

        # Phase 8：权限审批；deny 直接出错；allow 继续
        if self._permission is not None:
            decision = await self._permission.check(
                tool_name=call.name, arguments=call.arguments, session_id=session_id,
            )
            if decision.kind == "deny":
                msg = f"权限拒绝：{decision.reason or decision.rule or 'denied'}"
                _logger.warning(
                    "工具被权限拒绝",
                    tool=call.name, rule=decision.rule, danger=decision.danger,
                )
                return ToolResult(
                    call_id=call.id, name=call.name, content=msg, is_error=True,
                )

        try:
            result = await tool.execute(call.arguments)
        except Exception as exc:
            _logger.exception("工具执行异常", tool=call.name)
            return ToolResult(
                call_id=call.id, name=call.name,
                content=f"工具异常 {type(exc).__name__}: {exc}", is_error=True,
            )

        # 长输出截断
        if len(result.content) > _MAX_RESULT_CHARS:
            truncated_content = (
                result.content[:_MAX_RESULT_CHARS]
                + f"\n\n[... 输出被截断，原长 {len(result.content)} 字符 ...]"
            )
            return ToolResult(
                call_id=result.call_id, name=result.name,
                content=truncated_content, is_error=result.is_error, truncated=True,
            )
        return result
