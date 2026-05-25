"""微信 bot 编排器；后台 poll 队列 → 白名单/安全 → 引擎 → 发回。"""

from __future__ import annotations

import asyncio

from sanshiliu.channels.web.handlers import HealthState
from sanshiliu.channels.wechat.approvals import (
    WechatApprovalBroker,
    _current_wechat_user,
)
from sanshiliu.channels.wechat.ilink_client import ILinkClient
from sanshiliu.channels.wechat.queue import QueueItem, WechatQueue
from sanshiliu.channels.wechat.safety import WechatSafety
from sanshiliu.channels.wechat.whitelist import WechatWhitelist
from sanshiliu.engine.commands import CommandContext, is_slash_command, try_dispatch
from sanshiliu.engine.loop import ConversationEngine
from sanshiliu.engine.session import Session
from sanshiliu.foundation.errors import ChannelError
from sanshiliu.foundation.logging import get_logger
from sanshiliu.memory.shortterm import ShortTermMemory
from sanshiliu.storage.db import Database

_logger = get_logger(__name__)

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
        safety: WechatSafety,
        health: HealthState,
        short_term: ShortTermMemory | None = None,
        approval_broker: WechatApprovalBroker | None = None,
    ) -> None:
        self._db = db
        self._engine = engine
        self._client = client
        self._queue = queue
        self._whitelist = whitelist
        self._safety = safety
        self._health = health
        self._short_term = short_term
        self._approval_broker = approval_broker
        self._stop = asyncio.Event()
        self._consume_task: asyncio.Task[None] | None = None
        self._ping_task: asyncio.Task[None] | None = None
        self._session_cache: dict[str, Session] = {}
        self._inflight: set[asyncio.Task[None]] = set()
        # 已 spawn 但未 mark_done 的 item.id；防止 consume_loop 在 mark_done 写库前
        # 反复捞到同一条消息触发 N 个并发 handle_one（曾导致 200+ 限流刷屏 + iLink -2 风暴）
        self._claimed_item_ids: set[int] = set()

    async def start(self) -> None:
        if self._consume_task is not None:
            return
        self._stop.clear()
        # 让 broker 能用 client.send_text 发审批提示给用户
        if self._approval_broker is not None:
            self._approval_broker.bind_sender(self._send_to_user)
        self._consume_task = asyncio.create_task(self._consume_loop(), name="wechat-consume")
        self._ping_task = asyncio.create_task(self._ping_loop(), name="wechat-ping")
        _logger.info("wechat bot 启动", whitelist_size=self._whitelist.size)

    async def _send_to_user(self, user_id: str, text: str) -> None:
        """供 approval broker 向某用户单发文本；不落出站日志。"""
        try:
            await self._client.send_text(user_id, text)
        except ChannelError as exc:
            _logger.warning("wechat 审批提示发送失败", user_id=user_id, error=str(exc))

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
                # 排除已派发但未 mark_done 的，让 fetch_next 跳到 next-newer
                # 否则当 handle_one 阻塞在 confirm 上时，fetch_next 永远返这条
                # 在途消息 → 后续审批回复永远捞不到 → 死锁
                item = await self._queue.fetch_next(
                    exclude_ids=set(self._claimed_item_ids),
                )
            except Exception as exc:
                _logger.error("拉取队列失败", error=str(exc))
                await self._queue.wait_until_stop(self._stop)
                continue
            if item is None:
                await self._queue.wait_until_stop(self._stop)
                continue

            # 先看是不是审批回复（如 /同意 /拒绝），是则直接解决 broker future
            # 不再当成新一轮对话送进引擎；否则后台 task 处理新消息
            if (
                self._approval_broker is not None
                and self._approval_broker.try_consume(item.user_id, item.content)
            ):
                await self._queue.mark_done(item.id)
                _logger.info(
                    "wechat 审批回复已消费",
                    user_id=item.user_id, item_id=item.id, content=item.content[:20],
                )
                continue

            # 并行调度：handle_one 可能阻塞在 confirm() 上，sequential 会导致
            # 后续审批回复无法被 consume_loop 看到 → 死锁
            # 先 claim 再 spawn；handle_one 写完 mark_done 后由 done_callback 释放
            self._claimed_item_ids.add(item.id)
            task = asyncio.create_task(self._handle_one(item))
            self._inflight.add(task)
            item_id = item.id
            def _release(t: asyncio.Task[None], iid: int = item_id) -> None:
                self._inflight.discard(t)
                self._claimed_item_ids.discard(iid)
            task.add_done_callback(_release)

    async def _handle_one(self, item: QueueItem) -> None:
        # fail-safe：未捕获异常也要把 item 推进到 processed != 0，否则 fetch_next
        # 会一直返回它 → done_callback 释放 claim → consume_loop 再次 spawn → 无限循环
        try:
            await self._handle_one_inner(item)
        except Exception as exc:
            _logger.exception("wechat handle_one 未捕获异常", item_id=item.id, error=str(exc))
            try:
                await self._queue.mark_failed(item.id, f"unhandled: {type(exc).__name__}: {exc}")
            except Exception as mark_exc:
                _logger.error("mark_failed 也失败", item_id=item.id, error=str(mark_exc))

    async def _handle_one_inner(self, item: QueueItem) -> None:
        # 白名单：非白名单只 mark_done 不调 LLM 不回复（V-4）
        if not self._whitelist.allows(item.user_id):
            await self._queue.mark_done(item.id)
            return

        # 输入安全：命中黑名单直接吃掉（V-9）
        in_check = self._safety.check_input(item.content)
        if in_check.blocked:
            await self._queue.mark_done(item.id)
            return

        # slash 命令短路（/new /compact /help ...）：不走 LLM，直接回 reply
        session = self._session_cache.setdefault(item.session_id, self._build_session(item))
        if is_slash_command(item.content):
            cmd_ctx = CommandContext(session=session, engine=self._engine, channel="wechat")
            result = await try_dispatch(item.content, cmd_ctx)
            if result is not None:
                await self._send_safe(item, result.reply)
                if self._short_term is not None:
                    try:
                        await self._short_term.snapshot(session)
                    except Exception as exc:
                        _logger.warning("wechat 命令后 snapshot 失败", item_id=item.id, error=str(exc))
                await self._queue.mark_done(item.id)
                return

        # 跑引擎；contextvar 让 CompositeConfirmer 路由到 wechat broker
        token = _current_wechat_user.set(item.user_id)
        try:
            msg = await self._engine.complete_turn(session, item.content)
            reply = msg.content or ""
            if self._short_term is not None:
                try:
                    await self._short_term.snapshot(session)
                except Exception as exc:
                    _logger.warning("wechat 会话快照失败（不阻塞）", item_id=item.id, error=str(exc))
        except Exception as exc:
            _logger.error("引擎处理 wechat 消息失败", item_id=item.id, error=str(exc))
            await self._queue.mark_failed(item.id, str(exc))
            return
        finally:
            _current_wechat_user.reset(token)

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
        # 官方模式下 client.ping() 是 no-op 总返 True，盲写 "up" 会覆盖长轮询设的 "expired"
        # 仅在本地 webhook 模式（有真实 ping 端点）下才主动探活
        if self._client.official:
            return
        while not self._stop.is_set():
            ok = await self._client.ping()
            # 不覆盖长轮询设的 expired/down；只在当前是 unknown 或同源 up/down 时更新
            current = self._health.snapshot()["components"].get("wechat", "unknown")
            if current not in ("expired", "down") or ok:
                self._health.set("wechat", "up" if ok else "down")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=_PING_INTERVAL_SEC)
                break
            except TimeoutError:
                continue
