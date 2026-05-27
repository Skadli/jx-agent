"""三个 endpoint 实现：/chat (SSE) / /healthz / /metrics。

Phase 10：/chat 支持多模态——`{"q": "...", "images": ["data:image/...;base64,..."]}`。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import queue as q_mod
import re
import threading
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from sanshiliu.channels.web.approvals import WebApprovalBroker
from sanshiliu.channels.web.sse import format_event, safe_write
from sanshiliu.context.manager import ContextManager
from sanshiliu.engine.commands import CommandContext, is_slash_command, try_dispatch
from sanshiliu.engine.loop import ConversationEngine
from sanshiliu.engine.session import Session
from sanshiliu.engine.types import MessageContent
from sanshiliu.foundation.logging import get_logger
from sanshiliu.foundation.msg_split import StreamingSplitter
from sanshiliu.memory.shortterm import ShortTermMemory
from sanshiliu.storage.db import Database

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler

_logger = get_logger(__name__)

# 后台心跳间隔；过短会刷屏，过长易被代理切断
_HEARTBEAT_INTERVAL_SEC = 15.0
# 单次 /chat 最大时长；超过强制断开
_CHAT_DEADLINE_SEC = 120.0

# Phase 10 多模态：data: URI 白名单 + 解析正则
# 形如 "data:image/jpeg;base64,/9j/4AAQ..."
_DATA_URI_RE = re.compile(
    r"^data:(image/(?:jpeg|jpg|png|webp));base64,([A-Za-z0-9+/=\s]+)$",
    re.IGNORECASE,
)


class MultimodalValidationError(ValueError):
    """多模态 payload 解析失败；handler 捕获后回 400。"""


def _validate_data_uri(
    uri: str, *, max_decoded_bytes: int,
) -> tuple[str, int]:
    """校验 data:image 白名单类型和解码后字节数；返回 (规范化后的 URI, decoded_bytes)。

    decode 后字节数超 max 抛 MultimodalValidationError。base64 非法同理。
    """
    if not isinstance(uri, str):
        raise MultimodalValidationError("image must be data: URI string")
    m = _DATA_URI_RE.match(uri.strip())
    if m is None:
        raise MultimodalValidationError(
            "image must be data:image/{jpeg,png,webp};base64,..."
        )
    b64 = re.sub(r"\s", "", m.group(2))
    try:
        decoded = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MultimodalValidationError(f"invalid base64: {exc}") from exc
    if len(decoded) > max_decoded_bytes:
        raise MultimodalValidationError(
            f"image too large: {len(decoded)} bytes > limit {max_decoded_bytes}"
        )
    # 规范化：去掉白空白后回写
    mime = m.group(1).lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    normalized = f"data:{mime};base64,{b64}"
    return normalized, len(decoded)


def _build_multimodal_content(
    question: str,
    images: list[str],
    *,
    max_images: int,
    max_image_bytes: int,
) -> MessageContent:
    """把 q + images 拼成 OpenAI 多模态 content。

    无 image → 返 str（保持 Phase 1-9 行为）；有 image → 返 list[dict]。
    """
    if not images:
        return question
    if len(images) > max_images:
        raise MultimodalValidationError(
            f"too many images: {len(images)} > limit {max_images}"
        )
    parts: list[dict[str, Any]] = []
    if question.strip():
        parts.append({"type": "text", "text": question})
    for raw in images:
        uri, _ = _validate_data_uri(raw, max_decoded_bytes=max_image_bytes)
        parts.append({"type": "image_url", "image_url": {"url": uri}})
    return parts


class HealthState:
    """运行时各模块健康标志；线程安全简单读写。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, str] = {
            "web": "up",
            "llm": "unknown",
            "db": "unknown",
            "wechat": "disabled",
        }
        self._last_update: dict[str, float] = {}

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._state[key] = value
            self._last_update[key] = time.time()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "components": dict(self._state),
                "updated_at": dict(self._last_update),
            }


class SessionStore:
    """进程内会话缓存；按 session_id 复用 Session 对象，支持多轮上下文。"""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str | None, channel: str = "web") -> Session:
        with self._lock:
            if session_id and session_id in self._sessions:
                return self._sessions[session_id]
            if session_id:
                sess = Session(session_id=session_id, channel=channel)
            else:
                sess = Session.new(channel=channel)
            self._sessions[sess.session_id] = sess
            return sess

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(session_id)

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def all_ids(self) -> list[str]:
        with self._lock:
            return list(self._sessions.keys())


def make_chat_handler(
    engine: ConversationEngine,
    loop: asyncio.AbstractEventLoop,
    health: HealthState,
    session_store: SessionStore,
    short_term: ShortTermMemory | None = None,
    approval_broker: WebApprovalBroker | None = None,
    *,
    multimodal_max_images: int = 4,
    multimodal_max_image_bytes: int = 5 * 1024 * 1024,
) -> Callable[[BaseHTTPRequestHandler], None]:
    """构造 /chat (POST + SSE) handler；engine / store / 持久化由外部注入。

    Phase 10：接收 `{"q": str, "images"?: [data:URI], "session_id"?: str}`；
    images 数量上限和单图大小上限来自 Settings。
    """
    # base64 比原始字节大 ~33%；预留 64KB 给 text + JSON 包装
    body_size_limit = max(
        64 * 1024,
        int(multimodal_max_image_bytes * multimodal_max_images * 4 / 3) + 64 * 1024,
    )

    def handler(req: BaseHTTPRequestHandler) -> None:
        length = int(req.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > body_size_limit:
            req.send_error(413 if length > body_size_limit else 400, "missing or oversized body")
            return
        try:
            body = json.loads(req.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            req.send_error(400, "invalid JSON")
            return

        question = (body or {}).get("q") or (body or {}).get("query") or ""
        if not isinstance(question, str):
            req.send_error(400, "missing field: q")
            return
        images_raw = (body or {}).get("images") or []
        if not isinstance(images_raw, list):
            req.send_error(400, "field 'images' must be an array")
            return
        # 纯文本场景下 q 不能为空；有图片时 q 可以空
        if not images_raw and not question.strip():
            req.send_error(400, "missing field: q")
            return

        try:
            user_content: MessageContent = _build_multimodal_content(
                question, images_raw,
                max_images=multimodal_max_images,
                max_image_bytes=multimodal_max_image_bytes,
            )
        except MultimodalValidationError as exc:
            req.send_error(400, f"invalid multimodal payload: {exc}")
            return

        explicit_sid = (body or {}).get("session_id")
        if not isinstance(explicit_sid, str):
            explicit_sid = None

        # SSE 响应头；X-Accel-Buffering=no 禁止 Nginx 缓冲
        req.send_response(200)
        req.send_header("Content-Type", "text/event-stream; charset=utf-8")
        req.send_header("Cache-Control", "no-cache, no-transform")
        req.send_header("Connection", "close")
        req.send_header("X-Accel-Buffering", "no")
        req.end_headers()

        # 跨线程把 async generator 转 sync queue；SENTINEL 标记结束
        sse_q: q_mod.Queue[Any] = q_mod.Queue()
        SENTINEL = object()
        ERROR = object()
        APPROVAL = object()
        MSG_BREAK = object()  # <MSG> 切到的段边界；前端收到 event=msg_break 时开新气泡

        session = session_store.get_or_create(explicit_sid, channel="web")
        # 先把 session_id 推到客户端，便于前端跟踪
        safe_write(req.wfile, format_event(session.session_id, event="session"))

        async def _produce() -> None:
            approval_token = None
            if approval_broker is not None:
                approval_token = approval_broker.bind_emitter(
                    lambda payload: sse_q.put((APPROVAL, payload))
                )
            try:
                # slash 命令短路：不走 LLM，直接回 reply（仅纯文本路径生效）
                q = question.strip()
                if not images_raw and is_slash_command(q):
                    cmd_ctx = CommandContext(
                        session=session, engine=engine, channel="web",
                        short_term=short_term,
                    )
                    result = await try_dispatch(q, cmd_ctx)
                    if result is not None:
                        sse_q.put(result.reply)
                        # 也写一次 snapshot，让 dashboard 历史能反映 /new 清空、/compact 摘要等结果
                        if short_term is not None:
                            try:
                                await short_term.snapshot(session)
                            except Exception as exc:
                                _logger.warning("snapshot 失败（不阻塞）", error=str(exc))
                        sse_q.put(SENTINEL)
                        return

                sp = StreamingSplitter()
                first_segment = True
                async for delta in engine.stream_turn(session, user_content):
                    for seg in sp.feed(delta.text):
                        if not first_segment:
                            sse_q.put(MSG_BREAK)
                        sse_q.put(seg)
                        first_segment = False
                for seg in sp.close():
                    if not first_segment:
                        sse_q.put(MSG_BREAK)
                    sse_q.put(seg)
                    first_segment = False
                # 落 jsonl 用于回放和 dashboard 历史展示
                if short_term is not None:
                    try:
                        await short_term.snapshot(session)
                    except Exception as exc:
                        _logger.warning("snapshot 失败（不阻塞）", error=str(exc))
                sse_q.put(SENTINEL)
            except Exception as exc:
                _logger.error("/chat 流式生成失败", error=str(exc))
                sse_q.put((ERROR, f"{type(exc).__name__}: {exc}"))
                sse_q.put(SENTINEL)
            finally:
                if approval_broker is not None and approval_token is not None:
                    approval_broker.reset_emitter(approval_token)

        future = asyncio.run_coroutine_threadsafe(_produce(), loop)

        # 读 queue → 写 SSE，过期或断管退出
        deadline = time.monotonic() + _CHAT_DEADLINE_SEC
        last_beat = time.monotonic()
        try:
            while True:
                if time.monotonic() > deadline:
                    safe_write(req.wfile, format_event("deadline exceeded", event="error"))
                    break
                # 心跳
                if time.monotonic() - last_beat > _HEARTBEAT_INTERVAL_SEC:
                    if not safe_write(req.wfile, b": heartbeat\n\n"):
                        break
                    last_beat = time.monotonic()
                try:
                    item = sse_q.get(timeout=0.5)
                except q_mod.Empty:
                    continue
                if item is SENTINEL:
                    safe_write(req.wfile, format_event("", event="done"))
                    break
                if item is MSG_BREAK:
                    # 多消息拆分边界；前端按此事件开新气泡
                    if not safe_write(req.wfile, format_event("", event="msg_break")):
                        break
                    last_beat = time.monotonic()
                    continue
                if isinstance(item, tuple) and item[0] is ERROR:
                    safe_write(req.wfile, format_event(str(item[1]), event="error"))
                    break
                if isinstance(item, tuple) and item[0] is APPROVAL:
                    payload = json.dumps(item[1], ensure_ascii=False, default=str)
                    safe_write(req.wfile, format_event(payload, event="approval"))
                    last_beat = time.monotonic()
                    continue
                if not safe_write(req.wfile, format_event(str(item))):
                    break
                last_beat = time.monotonic()
        finally:
            # 确保 coroutine 在客户端断开后仍能跑完（已 yield 的 sse_q 数据让它消化掉）
            with contextlib.suppress(Exception):
                future.result(timeout=5.0)
            req.close_connection = True

        health.set("llm", "up")

    return handler


def make_healthz_handler(
    db: Database,
    loop: asyncio.AbstractEventLoop,
    health: HealthState,
) -> Callable[[BaseHTTPRequestHandler], None]:
    """构造 /healthz handler；同步返回 4 个组件状态。"""

    def handler(req: BaseHTTPRequestHandler) -> None:
        # DB ping：通过桥跑一个轻量查询
        async def _ping_db() -> bool:
            try:
                cur = await db._execute("SELECT 1 AS ok")
                row = cur.fetchone()
                return bool(row and row["ok"] == 1)
            except Exception:
                return False

        try:
            fut = asyncio.run_coroutine_threadsafe(_ping_db(), loop)
            db_ok = fut.result(timeout=3.0)
        except Exception:
            db_ok = False
        health.set("db", "up" if db_ok else "down")

        snap = health.snapshot()
        all_up = all(v in ("up", "disabled") for v in snap["components"].values())
        status = 200 if all_up else 503

        payload = json.dumps(snap, ensure_ascii=False).encode("utf-8")
        req.send_response(status)
        req.send_header("Content-Type", "application/json; charset=utf-8")
        req.send_header("Content-Length", str(len(payload)))
        req.end_headers()
        req.wfile.write(payload)

    return handler


def make_metrics_handler(
    context_manager: ContextManager | None,
) -> Callable[[BaseHTTPRequestHandler], None]:
    """构造 /metrics handler；返回 budget 统计 JSON。"""

    def handler(req: BaseHTTPRequestHandler) -> None:
        stats = context_manager.stats() if context_manager is not None else {}
        payload = json.dumps({"budget": stats, "ts": int(time.time())}, ensure_ascii=False).encode("utf-8")
        req.send_response(200)
        req.send_header("Content-Type", "application/json; charset=utf-8")
        req.send_header("Content-Length", str(len(payload)))
        req.end_headers()
        req.wfile.write(payload)

    return handler


def make_webhook_handler(
    process_fn: Callable[[bytes, dict[str, str]], Awaitable[tuple[int, str]]],
    loop: asyncio.AbstractEventLoop,
) -> Callable[[BaseHTTPRequestHandler], None]:
    """构造 /wechat/webhook handler；HMAC 校验由 process_fn 内部完成。"""

    def handler(req: BaseHTTPRequestHandler) -> None:
        length = int(req.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > 256 * 1024:
            req.send_error(400, "missing or oversized body")
            return
        body = req.rfile.read(length)
        headers = {k: v for k, v in req.headers.items()}

        try:
            fut = asyncio.run_coroutine_threadsafe(process_fn(body, headers), loop)
            status, msg = fut.result(timeout=10.0)
        except Exception as exc:
            _logger.error("/wechat/webhook 处理失败", error=str(exc))
            status, msg = 500, "internal error"

        payload = msg.encode("utf-8")
        req.send_response(status)
        req.send_header("Content-Type", "text/plain; charset=utf-8")
        req.send_header("Content-Length", str(len(payload)))
        req.end_headers()
        req.wfile.write(payload)

    return handler
