"""微信侧工具审批 broker；让 wechat 用户通过 /confirm /always /refuse 批准 LLM 的工具调用。

设计要点：
- `_current_wechat_user` contextvar 由 bot._handle_one 在进入引擎前 set；
  CompositeConfirmer 据此把 confirm 请求路由到本 broker，而非 web SSE。
- broker.request 先发一条 wechat 消息描述工具+参数+风险，再 await Future。
- bot.consume_loop 在派发新消息前先调 try_consume：若是审批关键词且用户有 pending，
  直接解决对应 Future，不再当成新对话发到引擎。
- 90 秒超时；超时按拒绝处理并通知用户。

命令语义：
- /confirm：只批准本次工具调用
- /always：本会话内同类工具调用全部放行（写 settings.json session-scope 缓存）
- /refuse：拒绝本次
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import threading
from collections.abc import Awaitable, Callable

from sanshiliu.foundation.logging import get_logger
from sanshiliu.security.bash_classifier import label as danger_label
from sanshiliu.security.types import ConfirmRequest, ConfirmResponse

_logger = get_logger(__name__)

_APPROVAL_TIMEOUT_SEC = 90.0

# 审批关键词；任意命中即视为相应决策；大小写不敏感、前后空白会被 strip
# 三组语义不同：confirm=once、always=session、refuse=deny。
_CONFIRM_KEYWORDS = {"/confirm", "confirm"}
_ALWAYS_KEYWORDS = {"/always", "always"}
_REFUSE_KEYWORDS = {"/refuse", "refuse"}

# 当前正在处理 wechat 消息的用户 id；handle_one 进入时 set
_current_wechat_user: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_wechat_user", default=None,
)

# 给 broker 注入"对某用户发文本"的能力；解耦避免循环导入 bot
SendTextFn = Callable[[str, str], Awaitable[None]]


class WechatApprovalBroker:
    """跨 task 协调待审批的工具调用。"""

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[ConfirmResponse]] = {}
        self._lock = threading.Lock()
        self._send_text: SendTextFn | None = None

    def bind_sender(self, fn: SendTextFn) -> None:
        """bot 启动时把发消息回调注入进来。"""
        self._send_text = fn

    async def request(self, user_id: str, request: ConfirmRequest) -> ConfirmResponse:
        """阻塞当前协程，直到用户回复或超时；超时按拒绝处理。"""
        if self._send_text is None:
            _logger.warning("wechat 审批 broker 未绑定 sender，按拒绝处理", tool=request.tool_name)
            return ConfirmResponse(decision="deny", scope="once")

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[ConfirmResponse] = loop.create_future()
        with self._lock:
            # 同一用户如有未决审批，先把旧的标记为 deny 让前一个工具调用尽快收尾
            prev = self._pending.get(user_id)
            if prev is not None and not prev.done():
                prev.set_result(ConfirmResponse(decision="deny", scope="once"))
            self._pending[user_id] = fut

        try:
            prompt = self._format_prompt(request)
            try:
                await self._send_text(user_id, prompt)
            except Exception as exc:
                _logger.warning("wechat 工具审批提示发送失败", error=str(exc))
                return ConfirmResponse(decision="deny", scope="once")
            return await asyncio.wait_for(fut, timeout=_APPROVAL_TIMEOUT_SEC)
        except TimeoutError:
            with contextlib.suppress(Exception):
                await self._send_text(
                    user_id,
                    "（90 秒未回复 /confirm /always /refuse，已自动取消该工具调用）",
                )
            return ConfirmResponse(decision="deny", scope="once")
        finally:
            with self._lock:
                self._pending.pop(user_id, None)

    def try_consume(self, user_id: str, text: str) -> bool:
        """如果 text 是审批回复且用户有 pending，解决并返 True；否则 False。
        返回 True 时调用方应该把这条消息视为已消费，不再投递给引擎。

        关键词映射：
        - /confirm → allow once（仅本次）
        - /always  → allow session（本会话同类工具全部放行）
        - /refuse  → deny once
        """
        with self._lock:
            fut = self._pending.get(user_id)
        if fut is None or fut.done():
            return False
        stripped = text.strip().lower()
        if stripped in _CONFIRM_KEYWORDS:
            response = ConfirmResponse(decision="allow", scope="once")
        elif stripped in _ALWAYS_KEYWORDS:
            response = ConfirmResponse(decision="allow", scope="session")
        elif stripped in _REFUSE_KEYWORDS:
            response = ConfirmResponse(decision="deny", scope="once")
        else:
            return False

        def _set() -> None:
            if not fut.done():
                fut.set_result(response)
        fut.get_loop().call_soon_threadsafe(_set)
        return True

    @staticmethod
    def _format_prompt(req: ConfirmRequest) -> str:
        d = danger_label(req.danger) if req.danger else ""
        return (
            "⚙️ 工具调用需要你授权\n"
            f"工具：{req.canonical_name}（{req.tool_name}）\n"
            + (f"风险：{d}\n" if d else "")
            + f"参数：{req.arguments_preview[:200]}\n\n"
            "回复：/confirm 批准本次；/always 本会话内全部放行；/refuse 取消"
            "（90 秒内有效）"
        )


class WechatApprovalConfirmer:
    """挂在 PermissionManager 上的 Confirmer 适配器。
    根据 contextvar 找到当前 wechat 用户，把请求转交 broker。
    """

    def __init__(self, broker: WechatApprovalBroker) -> None:
        self._broker = broker

    async def confirm(self, request: ConfirmRequest) -> ConfirmResponse:
        user_id = _current_wechat_user.get()
        if not user_id:
            _logger.info("wechat 工具审批无活跃用户上下文，按拒绝处理", tool=request.tool_name)
            return ConfirmResponse(decision="deny", scope="once")
        return await self._broker.request(user_id, request)
