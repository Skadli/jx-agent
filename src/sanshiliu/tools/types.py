"""tool 层公共类型；与 OpenAI tool_calls 协议对齐。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ToolDef:
    """工具定义；name + description + JSON Schema input_schema 三件套。"""

    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema (parameters)

    def to_openai(self) -> dict[str, Any]:
        """转 OpenAI function tool 格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


@dataclass(frozen=True)
class ToolCall:
    """LLM 发起的工具调用；id 用于配对返回。"""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    """工具执行结果；is_error=True 时 LLM 会感知到失败。"""

    call_id: str
    name: str
    content: str
    is_error: bool = False
    truncated: bool = False  # bash/file_read 超长截断标记

    def to_openai(self) -> dict[str, Any]:
        """转 OpenAI tool 消息格式。"""
        return {
            "role": "tool",
            "tool_call_id": self.call_id,
            "name": self.name,
            "content": self.content,
        }


@runtime_checkable
class Tool(Protocol):
    """工具协议；所有内置/外置工具实现此协议。"""

    @property
    def definition(self) -> ToolDef: ...

    async def execute(self, args: dict[str, Any]) -> ToolResult: ...


# 工具执行包装：让函数式工具也能套进 Tool 协议
@dataclass
class FunctionTool:
    """把一个 async 函数包成 Tool；与 ToolDef 解耦。"""

    _def: ToolDef
    _fn: Callable[[dict[str, Any]], Awaitable[ToolResult]]

    @property
    def definition(self) -> ToolDef:
        return self._def

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        return await self._fn(args)


@dataclass
class ToolLoopState:
    """跨 tool 轮的状态；用于 dedupe 同一 (name, args) 重复调用与 max_turns 上限。"""

    max_turns: int = 10
    turn: int = 0
    seen_calls: dict[str, int] = field(default_factory=dict)

    def fingerprint(self, name: str, arguments: dict[str, Any]) -> str:
        """生成 (name, args) 指纹；arguments 中字段排序后 JSON 化。"""
        import json
        return f"{name}::{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"

    def remember(self, name: str, arguments: dict[str, Any]) -> int:
        """记录一次调用，返回该指纹累计次数（含本次）。"""
        fp = self.fingerprint(name, arguments)
        self.seen_calls[fp] = self.seen_calls.get(fp, 0) + 1
        return self.seen_calls[fp]
