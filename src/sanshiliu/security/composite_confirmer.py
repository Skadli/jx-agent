"""按 channel 上下文路由的 Confirmer；让 PermissionManager 同时服务 web 和 wechat。

为什么需要：PermissionManager 只持有一个 confirmer 实例，但 web/wechat 通道
的 UI 完全不同（SSE 卡片 vs 微信对话消息）。运行时通过 contextvars 判定
当前请求来自哪个通道，分发到对应 broker。
"""

from __future__ import annotations

from sanshiliu.foundation.logging import get_logger
from sanshiliu.security.types import Confirmer, ConfirmRequest, ConfirmResponse

_logger = get_logger(__name__)


class CompositeConfirmer:
    """复合 confirmer：优先成长自动放行（无人值守、免审批 #5），
    其次 wechat（如果 contextvar 标记是 wechat 入口），再次 web；
    都没绑定就回退到 fallback（默认 deny）。
    """

    def __init__(
        self,
        *,
        web: Confirmer | None = None,
        wechat: Confirmer | None = None,
        fallback: Confirmer | None = None,
        growth: Confirmer | None = None,
    ) -> None:
        self._web = web
        self._wechat = wechat
        self._fallback = fallback
        # 成长后台自动放行（PR3 #5）；仅当 _current_growth contextvar 为真时启用，
        # 由 GrowthRunner 严格圈定在一次成长运行内。缺省 None 则成长上下文回落 fallback(deny)。
        self._growth = growth

    async def confirm(self, request: ConfirmRequest) -> ConfirmResponse:
        # 延迟 import 避免 channels / scheduler 反向依赖 security
        from sanshiliu.channels.web.approvals import _current_emitter
        from sanshiliu.channels.wechat.approvals import _current_wechat_user
        from sanshiliu.security.growth_approvals import in_growth_autoallow

        # 成长自动放行最优先：凌晨无人值守，Skill(skill-finder)/installer 的工具调用
        # 必须免审批通过（#5）。注意 deny-pattern / critical 硬底线在 PermissionManager.check
        # 里 ask 之前就返回，本分支接触不到——危险命令仍被拦，放行只覆盖会询问的那批。
        if in_growth_autoallow() and self._growth is not None:
            return await self._growth.confirm(request)
        if _current_wechat_user.get() and self._wechat is not None:
            return await self._wechat.confirm(request)
        if _current_emitter.get() is not None and self._web is not None:
            return await self._web.confirm(request)
        if self._fallback is not None:
            return await self._fallback.confirm(request)
        _logger.info(
            "CompositeConfirmer 无可用 backend，按拒绝处理",
            tool=request.tool_name,
        )
        return ConfirmResponse(decision="deny", scope="once")
