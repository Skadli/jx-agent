"""ToolDispatcher + parse_tool_calls 单测（V-5 + V-6 边界）。"""

from __future__ import annotations

import json

from sanshiliu.tools.dispatcher import ToolDispatcher, parse_tool_calls
from sanshiliu.tools.registry import ToolRegistry
from sanshiliu.tools.types import FunctionTool, ToolCall, ToolDef, ToolResult


def _registry_with(name: str, schema: dict, fn) -> ToolRegistry:
    r = ToolRegistry()
    r.register(FunctionTool(ToolDef(name=name, description="d", input_schema=schema), fn))
    return r


def test_parse_tool_calls_basic() -> None:
    raw = [{"id": "c1", "function": {"name": "foo", "arguments": '{"x": 1}'}}]
    out = parse_tool_calls(raw)
    assert out[0].id == "c1"
    assert out[0].name == "foo"
    assert out[0].arguments == {"x": 1}


def test_parse_tool_calls_bad_json() -> None:
    raw = [{"id": "c1", "function": {"name": "foo", "arguments": "not-json"}}]
    out = parse_tool_calls(raw)
    assert out[0].arguments == {}


async def test_dispatch_unknown_tool() -> None:
    d = ToolDispatcher(ToolRegistry())
    res = await d.execute(ToolCall(id="c1", name="ghost", arguments={}))
    assert res.is_error
    assert "未知工具" in res.content


async def test_dispatch_missing_required_field() -> None:
    """V-5 类似：schema 缺字段 → tool_error 不崩。"""
    async def _run(args):
        return ToolResult("", "t", "ok")
    reg = _registry_with("t", {"type": "object", "required": ["x"], "properties": {"x": {"type": "string"}}}, _run)
    d = ToolDispatcher(reg)
    res = await d.execute(ToolCall(id="c1", name="t", arguments={}))
    assert res.is_error
    assert "参数缺失" in res.content


async def test_dispatch_tool_exception_does_not_crash() -> None:
    async def _run(_):
        raise RuntimeError("boom")
    reg = _registry_with("t", {"type": "object"}, _run)
    d = ToolDispatcher(reg)
    res = await d.execute(ToolCall(id="c1", name="t", arguments={}))
    assert res.is_error
    assert "boom" in res.content
    assert "RuntimeError" in res.content


async def test_dispatch_truncates_long_output() -> None:
    big = "x" * 20_000
    async def _run(_):
        return ToolResult("c1", "t", big)
    reg = _registry_with("t", {"type": "object"}, _run)
    d = ToolDispatcher(reg)
    res = await d.execute(ToolCall(id="c1", name="t", arguments={}))
    assert res.truncated
    assert "输出被截断" in res.content
    assert len(res.content) < len(big)


async def test_dispatch_success_passthrough() -> None:
    async def _run(args):
        return ToolResult(call_id="c1", name="t", content=f"hello {args.get('x')}")
    reg = _registry_with("t", {"type": "object", "properties": {"x": {"type": "string"}}}, _run)
    d = ToolDispatcher(reg)
    res = await d.execute(ToolCall(id="c1", name="t", arguments={"x": "world"}))
    assert res.is_error is False
    assert res.content == "hello world"


def test_tool_loop_state_dedupe_counts() -> None:
    """V-6：连续同一调用计数累加；调用方据此决定 dedupe。"""
    from sanshiliu.tools.types import ToolLoopState
    s = ToolLoopState(max_turns=10)
    assert s.remember("foo", {"x": 1}) == 1
    assert s.remember("foo", {"x": 1}) == 2
    # 不同参数 → 独立计数
    assert s.remember("foo", {"x": 2}) == 1
    # JSON 顺序不影响指纹
    fp1 = s.fingerprint("foo", {"a": 1, "b": 2})
    fp2 = s.fingerprint("foo", {"b": 2, "a": 1})
    assert fp1 == fp2


def test_argument_dict_dump_roundtrip() -> None:
    """sanity：JSON 编解码不丢类型。"""
    args = {"q": "hello", "max": 5, "deep": {"nested": True}}
    assert json.loads(json.dumps(args)) == args
