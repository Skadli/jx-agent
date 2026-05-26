"""LoadPersonaModule：把 persona module 的正文拉到工具结果里给 LLM。

设计取舍：
- 引擎层 `_refresh_persona_modules` 默认按关键词自动注入 0-1 个 module；
  本工具是兜底——LLM 觉得引擎漏选/选错时主动补加载。
- 不做"已注入则拒绝"的精确去重——dispatcher 当前不传 session 引用进 execute，
  改造侵入式太大。LLM 在 listing 里被告知"引擎会自动注入"，重复调用属低概率事件。
- 不写审计表（无 persona_activations 表）；后续如需追踪可加。
"""

from __future__ import annotations

from typing import Any

from sanshiliu.foundation.logging import get_logger
from sanshiliu.identity.module_activator import PersonaModuleActivator
from sanshiliu.tools.types import ToolDef, ToolResult, _check_required_fields

_logger = get_logger(__name__)


class LoadPersonaModuleTool:
    """Tool 协议实现；持有 activator 闭包。"""

    def __init__(
        self,
        definition: ToolDef,
        activator: PersonaModuleActivator,
    ) -> None:
        self._def = definition
        self._activator = activator

    @property
    def definition(self) -> ToolDef:
        return self._def

    async def validate(self, args: dict[str, Any]) -> str | None:
        if (err := _check_required_fields(args, self._def.input_schema)) is not None:
            return err
        raw = str(args.get("name") or "").strip()
        if not raw:
            return "参数 name 不能为空"
        if self._activator.lookup(raw) is None:
            available = ", ".join(m.id for m in self._activator.list_all()) or "（无）"
            return f"未知 persona module：{raw}；可用：{available}"
        return None

    async def execute(
        self,
        args: dict[str, Any],
        *,
        session_id: str = "",
    ) -> ToolResult:
        raw = str(args.get("name") or "").strip()
        module = self._activator.lookup(raw)
        assert module is not None  # validate 保证
        body = self._activator.render_body(module)
        _logger.info(
            "persona module 由工具加载",
            module=module.id, session_id=session_id,
        )
        return ToolResult("", self._def.name, body, is_error=False)


def build_load_persona_module_tool(
    definition: ToolDef,
    activator: PersonaModuleActivator,
) -> LoadPersonaModuleTool:
    return LoadPersonaModuleTool(definition, activator)
