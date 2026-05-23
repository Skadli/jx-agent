"""微信 bot 编排器；后台 poll 队列 → 白名单/限流/安全 → 引擎 → 发回。"""

from __future__ import annotations

import asyncio

from sanshiliu.channels.web.handlers import HealthState
from sanshiliu.channels.wechat.ilink_client import ILinkClient
from sanshiliu.channels.wechat.queue import QueueItem, WechatQueue
from sanshiliu.channels.wechat.rate_limit import WechatRateLimiter
from sanshiliu.channels.wechat.safety import WechatSafety
from sanshiliu.channels.wechat.whitelist import WechatWhitelist
from sanshiliu.engine.loop import ConversationEngine
from sanshiliu.engine.session import Session
from sanshiliu.foundation.errors import ChannelError
from sanshiliu.foundation.logging import get_logger
from sanshiliu.storage.db import Database

_logger = get_logger(__name__)

# 用户被限流后给的简短提示；客服话术非 LLM prompt
_RATE_LIMIT_NOTICE_USER = "今天聊得有点多，明天再来找我吧。"
_RATE_LIMIT_NOTICE_GLOBAL = "我现在有点忙不过来，稍等一两分钟再说。"
# iLink 探活间隔；健康状态会写入 HealthState 给 /healthz 用
_PING_INTERVAL_SEC = 30.0


class WechatBot:
    """单实例后台任务；start() 起两个 task：消费队列 + 定期探活。"""

    def __init__(
        self,
        *,
        db: Database,
        engine: ConversationEngine,
        client: ILinkClient,
        queue: WechatQueue,
        whitelist: WechatWhitelist,
        rate_limiter: WechatRateLimiter,
        safety: WechatSafety,
        health: HealthState,
    ) -> None:
        self._db = db
        self._engine = engine
        self._client = client
        self._queue = queue
        self._whitelist = whitelist
        self._rate_limiter = rate_limiter
        self._safety = safety
        self._health = health
        self._stop = asyncio.Event()
        self._consume_task: asyncio.Task[None] | None = None
        self._ping_task: asyncio.Task[None] | None = None
        self._session_cache: dict[str, Session] = {}

    async def start(self) -> None:
        if self._consume_task is not None:
            return
        self._stop.clear()
        self._consume_task = asyncio.create_task(self._consume_loop(), name="wechat-consume")
        self._ping_task = asyncio.create_task(self._ping_loop(), name="wechat-ping")
        _logger.info("wechat bot 启动", whitelist_size=self._whitelist.size)

    async def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        for t in (self._consume_task, self._ping_task):
            if t is None:
                continue
            try:
                await asyncio.wait_for(t, timeout=timeout)
            except TimeoutError:
                t.cancel()
        self._consume_task = None
        self._ping_task = None
        _logger.info("wechat bot 已停止")

    async def _consume_loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = await self._queue.fetch_next()
            except Exception as exc:
                _logger.error("拉取队列失败", error=str(exc))
                await self._queue.wait_until_stop(self._stop)
                continue
            if item is None:
                await self._queue.wait_until_stop(self._stop)
                continue
            await self._handle_one(item)

    async def _handle_one(self, item: QueueItem) -> None:
        # 白名单：非白名单只 mark_done 不调 LLM 不回复（V-4）
        if not self._whitelist.allows(item.user_id):
            await self._queue.mark_done(item.id)
            return

        # 输入安全：命中黑名单直接吃掉（V-9）
        in_check = self._safety.check_input(item.content)
        if in_check.blocked:
            await self._queue.mark_done(item.id)
            return

        # 限流：日额超走 user 提示，分钟额超走 global 提示
        decision = await self._rate_limiter.take(item.user_id)
        if not decision.allowed:
            notice = _RATE_LIMIT_NOTICE_USER if decision.reason == "user_quota" else _RATE_LIMIT_NOTICE_GLOBAL
            await self._send_safe(item, notice)
            await self._queue.mark_done(item.id)
            return

        # 跑引擎
        session = self._session_cache.setdefault(item.session_id, self._build_session(item))
        try:
            msg = await self._engine.complete_turn(session, item.content)
            reply = msg.content or ""
        except Exception as exc:
            _logger.error("引擎处理 wechat 消息失败", item_id=item.id, error=str(exc))
            await self._queue.mark_failed(item.id, str(exc))
            return

        # 输出安全：命中走 redacted 文案
        out_check = self._safety.check_output(reply)
        final = out_check.redacted_text if out_check.blocked else reply

        await self._send_safe(item, final or "")
        await self._queue.mark_done(item.id)

    def _build_session(self, item: QueueItem) -> Session:
        """每个 wxid+group_id 复用同一个 Session；engine 自己刷 persona。"""
        return Session(
            session_id=item.session_id,
            channel="wechat",
            user_id=item.user_id,
        )

    async def _send_safe(self, item: QueueItem, text: str) -> None:
        """发出 + 落出站日志；iLink 故障不阻塞队列消费。"""
        target = item.group_id or item.user_id
        try:
            await self._client.send_text(target, text)
        except ChannelError as exc:
            _logger.error("iLink 发送失败", target=target, error=str(exc))
            return
        await self._queue.record_outbound(
            session_id=item.session_id,
            user_id=item.user_id,
            group_id=item.group_id,
            content=text,
            llm_call_id=None,
        )

    async def _ping_loop(self) -> None:
        while not self._stop.is_set():
            ok = await self._client.ping()
            self._health.set("wechat", "up" if ok else "down")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=_PING_INTERVAL_SEC)
                break
            except TimeoutError:
                continue
