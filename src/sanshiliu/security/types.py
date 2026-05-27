"""安全权限层公共类型；与 Claude settings.json 协议对齐。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

# 与 Claude 协议一致：allow / deny / ask；fail-open 时强制 ask（见 R9）
DefaultMode = Literal["allow", "deny", "ask"]
DEFAULT_MODES: tuple[DefaultMode, ...] = ("allow", "deny", "ask")

# 决策结果；ask 表示需要追加用户确认（dispatcher 处理）
DecisionKind = Literal["allow", "deny", "ask"]

# 决策作用域；与 prd settings 协议对齐
DecisionScope = Literal["once", "session", "permanent"]

# bash_classifier 危险级；critical 会带额外警示文案
DangerLevel = Literal["safe", "moderate", "dangerous", "critical"]

# 工具规范名 → 协议显示名映射（与 Claude 一致）；用于 settings.json 模式匹配
# 例：settings.json 写 "Bash(ls:*)" 对应运行时的 bash_exec 工具
TOOL_ALIASES: dict[str, str] = {
    "bash_exec": "Bash",
    "file_read": "Read",
    "file_write": "Write",
    "web_search": "WebSearch",
}


def canonical_tool_alias(tool_name: str) -> str:
    """运行时工具名 → settings 协议显示名；未知工具原样返回。"""
    return TOOL_ALIASES.get(tool_name, tool_name)


@dataclass(frozen=True)
class PermissionDecision:
    """权限检查输出；dispatcher 据此放行 / 拦截 / 询问。"""

    kind: DecisionKind
    rule: str | None = None  # 触发的 pattern（便于日志和报错）
    reason: str = ""
    danger: DangerLevel | None = None  # 仅 Bash 调用时填
    # PR3：决策来源，落 permission_decisions.source 用；区分自动 vs 用户确认
    source: str = "unknown"


@dataclass(frozen=True)
class PermissionSettings:
    """settings.json 中 permissions 段解析结果。"""

    default_mode: DefaultMode = "ask"
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    source_paths: tuple[Path, ...] = field(default_factory=tuple)  # 来源文件，便于 V-7


@dataclass(frozen=True)
class ConfirmRequest:
    """ask 模式下抛给用户的请求；通道层据此渲染 UI。"""

    tool_name: str
    canonical_name: str
    arguments_preview: str
    danger: DangerLevel | None = None
    matched_rule: str | None = None


@dataclass(frozen=True)
class ConfirmResponse:
    """用户对 ConfirmRequest 的回复。"""

    decision: Literal["allow", "deny"]
    scope: DecisionScope = "once"


@runtime_checkable
class Confirmer(Protocol):
    """用户确认回调；REPL 用 input() 实现，wechat/web 缺省走 always-deny。"""

    async def confirm(self, request: ConfirmRequest) -> ConfirmResponse: ...
