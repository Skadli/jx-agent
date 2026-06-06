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
    # PR3：权限决策结果（"allow" / "deny" / None）；engine._record_tool_call 写 tool_calls 表
    # None 表示没走 dispatcher（如 dedupe 路径）或没接 PermissionManager
    permission_decision: str | None = None

    def to_openai(self) -> dict[str, Any]:
        """转 OpenAI tool 消息格式。"""
        return {
            "role": "tool",
            "tool_call_id": self.call_id,
            "name": self.name,
            "content": self.content,
        }


def _check_required_fields(args: dict[str, Any], schema: dict[str, Any]) -> str | None:
    """默认 validate 实现：检查 input_schema.required 中的字段都在 args 里。
    工具自定义 validate 时可调用本函数复用默认行为，再叠加自身逻辑。"""
    required = schema.get("required") or []
    missing = [k for k in required if k not in args]
    if missing:
        return f"参数缺失：{', '.join(missing)}"
    return None


@runtime_checkable
class Tool(Protocol):
    """工具协议；所有内置/外置工具实现此协议。

    `validate` 在权限检查 + execute 之前由 dispatcher 调用，None=通过，错误描述=失败；
    把校验从 dispatcher 拉到工具自身（与 Claude Code SkillTool.validateInput 对齐），
    每个工具可以表达自己的前置约束（比如 Skill 工具检查 id 已注册）。

    session_id 是为审计写库（如 Skill 工具写 skill_activations）准备的；
    无状态工具（bash / file_io / web_search）可忽略。
    """

    @property
    def definition(self) -> ToolDef: ...

    async def validate(self, args: dict[str, Any]) -> str | None: ...

    async def execute(self, args: dict[str, Any], *, session_id: str = "") -> ToolResult: ...


# 工具执行包装：让函数式工具也能套进 Tool 协议
@dataclass
class FunctionTool:
    """把一个 async 函数包成 Tool；与 ToolDef 解耦。

    `_fn` 不感知 session_id —— 接口上接但忽略，避免逐个修内置工具签名。
    `validate` 走默认 required-field 检查；需要自定义校验的工具应实现独立类，不走本包装。
    """

    _def: ToolDef
    _fn: Callable[[dict[str, Any]], Awaitable[ToolResult]]

    @property
    def definition(self) -> ToolDef:
        return self._def

    async def validate(self, args: dict[str, Any]) -> str | None:
        return _check_required_fields(args, self._def.input_schema)

    async def execute(self, args: dict[str, Any], *, session_id: str = "") -> ToolResult:
        return await self._fn(args)


@dataclass
class ToolLoopState:
    """跨 tool 轮的状态；用于 dedupe 同一 (name, args) 重复调用与预算上限。"""

    max_turns: int = 10
    max_tool_calls: int = 8
    turn: int = 0
    tool_call_count: int = 0
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

    def consume_tool_call(self) -> bool:
        """记录一次模型请求的 tool_call；返回本次是否仍在总调用预算内。"""
        self.tool_call_count += 1
        return self.tool_call_count <= self.max_tool_calls
