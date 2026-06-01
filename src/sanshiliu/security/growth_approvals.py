"""成长会话的工具自动放行（PR3，落实用户决定 #5：外部 skill 自动安装、免人工审批）。

为什么需要：成长 session（channel="growth"）在凌晨 3 点无人值守地跑 engine.complete_turn，
LLM 会在 tool 循环里主动调 Skill(skill-finder)/Skill(skill-installer)，installer 又会经
bash 跑 git/npx 把真实 skill 拉进 skills/<id>/。这些调用走 PermissionManager 的 ask 路径，
而 CompositeConfirmer 在既无 web SSE、又无 wechat 用户上下文时**默认拒绝**——后台没人能批，
成长线就装不了任何 skill。

做法（与 web/wechat 完全对称）：
- 用一个 contextvar `_current_growth` 标记"当前协程正处在成长自动放行窗口内"；
- GrowthRunner 在 complete_turn 前后 set/reset 它，**严格只覆盖这一次成长运行**；
- CompositeConfirmer 见到该标记即把 confirm 路由到本模块的 GrowthAutoConfirmer，无条件 allow。

安全边界（已知并接受 #5 的供应链/注入风险，仍保留最低防护）：
- **不绕过 critical 硬底线 / settings.deny**：PermissionManager.check 里 deny-pattern 和
  critical-hard-deny 都在 ask 之前返回，本 confirmer 根本接触不到——rm -rf / mkfs 之类仍被拦。
  本放行只作用于"defaultMode=ask 才会询问"的那批调用（Skill 本身、git clone/npx 等非 critical）。
- **每次自动放行写一行审计日志**（tool + scope=auto），成长 tool 调用本就还会落 tool_calls /
  permission_decisions 表，双重留痕，dashboard/PR4 可回看装了什么。
- **全局 kill-switch = growth_enabled=false**：心跳任务默认不 enabled，整条成长线（含本放行）
  立即停摆——这是收回自动安装权限的总开关。
"""

from __future__ import annotations

import contextvars

from sanshiliu.foundation.logging import get_logger
from sanshiliu.security.types import ConfirmRequest, ConfirmResponse

_logger = get_logger(__name__)

# 当前协程是否处在"成长自动放行"窗口；仅 GrowthRunner 在 complete_turn 前后 set/reset。
# 默认 False = 任何非成长上下文都不受影响（web/wechat/REPL 行为完全不变）。
_current_growth: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "current_growth_autoallow",
    default=False,
)


def enter_growth_autoallow() -> contextvars.Token[bool]:
    """进入成长自动放行窗口；返回的 token 必须在 finally 里交给 exit 复位（防泄漏到别的请求）。"""
    return _current_growth.set(True)


def exit_growth_autoallow(token: contextvars.Token[bool]) -> None:
    """退出成长自动放行窗口；与 enter 配对，确保放行权限不外溢出本次成长运行。"""
    _current_growth.reset(token)


def in_growth_autoallow() -> bool:
    """CompositeConfirmer 路由判定用：当前是否在成长自动放行窗口内。"""
    return _current_growth.get()


class GrowthAutoConfirmer:
    """成长后台无人值守时的 Confirmer：对每个询问无条件放行（once 作用域），并写审计日志。

    只在 `_current_growth` 为真时被 CompositeConfirmer 选中；其余通道照旧走 web/wechat/deny。
    scope 用 once 而非 session——成长 session 一次性、不需要把放行缓存进 settings.json。
    """

    async def confirm(self, request: ConfirmRequest) -> ConfirmResponse:
        # 审计：每条自动放行留痕（落实 #5 的最低防护之一）。成长 tool 调用另会进 tool_calls 表。
        _logger.info(
            "成长会话工具自动放行（免审批 #5）",
            tool=request.tool_name,
            canonical=request.canonical_name,
            danger=request.danger,
            args_preview=request.arguments_preview[:120],
        )
        return ConfirmResponse(decision="allow", scope="once")
