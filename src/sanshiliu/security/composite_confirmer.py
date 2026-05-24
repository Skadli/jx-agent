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
    """复合 confirmer：优先 wechat（如果 contextvar 标记是 wechat 入口），
    其次 web；都没绑定就回退到 fallback（默认 deny）。
    """

    def __init__(
        self,
        *,
        web: Confirmer | None = None,
        wechat: Confirmer | None = None,
        fallback: Confirmer | None = None,
    ) -> None:
        self._web = web
        self._wechat = wechat
        self._fallback = fallback

    async def confirm(self, request: ConfirmRequest) -> ConfirmResponse:
        # 延迟 import 避免 channels 反向依赖 security
        from sanshiliu.channels.web.approvals import _current_emitter
        from sanshiliu.channels.wechat.approvals import _current_wechat_user

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
