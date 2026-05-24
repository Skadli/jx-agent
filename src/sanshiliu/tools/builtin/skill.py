"""Skill：把 SKILL.md 协议下的 skill 暴露成可被 LLM 调用的工具。

与 Claude Code 的 SkillTool 概念对齐：LLM 在 system prompt 中看到一份 name+description 列表，
判断需要时调本工具拿正文。命中写入 skill_activations（dashboard 用）。
"""

from __future__ import annotations

from typing import Any

from sanshiliu.foundation.logging import get_logger
from sanshiliu.skills.activator import SkillActivator
from sanshiliu.storage.db import Database
from sanshiliu.tools.types import ToolDef, ToolResult, _check_required_fields

_logger = get_logger(__name__)


class SkillTool:
    """Tool 协议实现；持有 activator + db 闭包。"""

    def __init__(
        self,
        definition: ToolDef,
        activator: SkillActivator,
        db: Database | None,
    ) -> None:
        self._def = definition
        self._activator = activator
        self._db = db

    @property
    def definition(self) -> ToolDef:
        return self._def

    async def validate(self, args: dict[str, Any]) -> str | None:
        """先走默认 required 检查，再检查 skill 非空且已注册——和 CC SkillTool.validateInput 同位。"""
        if (err := _check_required_fields(args, self._def.input_schema)) is not None:
            return err
        raw = str(args.get("skill") or "").strip()
        if not raw:
            return "参数 skill 不能为空"
        skill_id = raw[1:] if raw.startswith("/") else raw
        if self._activator.lookup(skill_id) is None:
            available = ", ".join(s.id for s in self._activator.list_all()) or "（无）"
            return f"未知 skill：{skill_id}；可用：{available}"
        return None

    async def execute(
        self,
        args: dict[str, Any],
        *,
        session_id: str = "",
    ) -> ToolResult:
        raw = str(args.get("skill") or "").strip()
        # 允许带前导斜杠以兼容 Claude /<skill> 习惯；validate 已保证 raw 非空且 skill 存在
        skill_id = raw[1:] if raw.startswith("/") else raw
        skill = self._activator.lookup(skill_id)
        assert skill is not None  # validate 保证

        if self._db is not None:
            try:
                await self._db.insert_skill_activation(
                    session_id=session_id,
                    skill_id=skill.id,
                    trigger="tool_call",
                    user_text=None,
                )
            except Exception as exc:
                # 审计写失败不阻塞主流程
                _logger.warning("skill_activations 写入失败", error=str(exc), skill=skill.id)

        _logger.info("skill 已激活", id=skill.id, source=str(skill.source))
        return ToolResult("", self._def.name, skill.body, is_error=False)


def build_skill_tool(
    definition: ToolDef,
    activator: SkillActivator,
    db: Database | None,
) -> SkillTool:
    return SkillTool(definition, activator, db)
