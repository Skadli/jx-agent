"""官方 iLink Bot getupdates 长轮询；把 Hermes 消息形态落入现有 sqlite 队列。"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sanshiliu.channels.web.handlers import HealthState
from sanshiliu.channels.wechat.ilink_client import ILINK_ERR_SESSION_TIMEOUT, ILinkClient
from sanshiliu.foundation.errors import ChannelError
from sanshiliu.foundation.logging import get_logger
from sanshiliu.storage.db import Database

_logger = get_logger(__name__)

_ITEM_TEXT = 1
_ITEM_IMAGE = 2
_ITEM_VOICE = 3
_ITEM_VIDEO = 4
_ITEM_FILE = 6
_ITEM_LINK = 7

# 非文本类型 → 占位文本；agent 看到占位至少能回复"看不到图/听不到语音"，
# 不会让消息静默丢掉（iLink 暂无媒体下载接口，真·多模态等加上后再说）
_NON_TEXT_PLACEHOLDERS: dict[int, str] = {
    _ITEM_IMAGE: "[图片]",
    _ITEM_VOICE: "[语音]",  # 仅 fallback：iLink 已 ASR 出 text 时优先走 text 分支
    _ITEM_VIDEO: "[视频]",
    _ITEM_FILE: "[文件]",
    _ITEM_LINK: "[链接]",
}
_POLL_RETRY_DELAY_SECONDS = 2.0
# session-expired 后的退避：避免对 iLink 发起持续无效请求 + 给用户重新扫码留时间
_EXPIRED_BACKOFF_SECONDS = 60.0


@dataclass(frozen=True)
class ILinkInboundMessage:
    peer_id: str
    user_id: str
    group_id: str | None
    message_id: str
    dedup_key: str
    text: str
    # 原始 image_item dict（未下载/未解密）；poller 拿到后异步走 client.download_image
    # 转 base64 data URI 再写入 channel_messages.media。frozenset 不可哈希 dict，用 tuple。
    image_items: tuple[dict[str, Any], ...] = ()


class ILinkLongPoller:
    """官方 iLink 长轮询任务；入站消息复用 WechatQueue 的消费链路。"""

    def __init__(
        self,
        *,
        db: Database,
        client: ILinkClient,
        account_id: str,
        health: HealthState,
        poll_timeout_ms: int,
        poll_interval_ms: int,
    ) -> None:
        self._db = db
        self._client = client
        self._account_id = account_id
        self._health = health
        self._poll_timeout_ms = poll_timeout_ms
        self._poll_interval = poll_interval_ms / 1000
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._sync_buf = ""
        self._seen: set[str] = set()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._sync_buf = await self._load_sync_buf()
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="wechat-ilink-poll")
        _logger.info("官方 iLink 长轮询已启动", account=self._short(self._account_id))

    async def stop(self, *, shutdown_timeout: float = 5.0) -> None:
        self._stop.set()
        if self._task is None:
            return
        try:
            await asyncio.wait_for(self._task, timeout=shutdown_timeout)
        except TimeoutError:
            self._task.cancel()
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                expired = await self._poll_once()
                if expired:
                    self._health.set("wechat", "expired")
                    wait_seconds = _EXPIRED_BACKOFF_SECONDS
                else:
                    self._health.set("wechat", "up")
                    wait_seconds = self._poll_interval
            except ChannelError as exc:
                self._health.set("wechat", "down")
                wait_seconds = _POLL_RETRY_DELAY_SECONDS
                _logger.warning("官方 iLink 轮询失败，将稍后重试", error=str(exc))
            except Exception as exc:
                self._health.set("wechat", "down")
                wait_seconds = _POLL_RETRY_DELAY_SECONDS
                _logger.exception("官方 iLink 轮询任务异常，将稍后重试", error=str(exc))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=wait_seconds)
            except TimeoutError:
                continue

    async def _poll_once(self) -> bool:
        """轮询一次；返回 True 表示 session 已过期需要退避 + 等用户重扫。"""
        response = await self._client.get_updates(
            self._sync_buf,
            timeout_ms=self._poll_timeout_ms,
        )

        # iLink 业务级错误 — 最常见的就是 -14 token 过期；不当作 ChannelError 抛
        # 而是返回 True 让上层退避，并明确告诉用户需要重扫码
        if isinstance(response, dict):
            errcode = response.get("errcode")
            if isinstance(errcode, int) and errcode != 0:
                errmsg = str(response.get("errmsg") or "")
                if errcode == ILINK_ERR_SESSION_TIMEOUT:
                    if not getattr(self, "_warned_expired", False):
                        _logger.warning(
                            "iLink session 已过期，请到 dashboard 设置页扫码重新登录；轮询已暂缓",
                            errcode=errcode, errmsg=errmsg,
                        )
                        self._warned_expired = True
                    return True
                # 其他业务级错误：警告但继续按正常间隔重试
                _logger.warning("iLink 业务错误", errcode=errcode, errmsg=errmsg)
                return False
            # 收到正常响应 → 清掉过期警告标志，下次过期还会再告警一次
            if getattr(self, "_warned_expired", False):
                self._warned_expired = False
                _logger.info("iLink session 已恢复正常")

        sync_buf = _extract_sync_buf(response)
        messages = _extract_inbound_messages(response, self._account_id)

        # 若 response 看起来非空但 parser 抽不出消息，把原始结构记下来
        # 帮助排查"连得上但收不到消息"的字段名差异
        if not messages and _looks_like_inbound_payload(response):
            import json as _json
            try:
                preview = _json.dumps(response, ensure_ascii=False)[:600]
            except Exception:
                preview = repr(response)[:600]
            _logger.warning(
                "iLink 返回疑似消息但未解析出来",
                top_keys=list(response.keys()) if isinstance(response, dict) else None,
                sync_buf_present=bool(sync_buf),
                preview=preview,
            )
        for message in messages:
            if message.dedup_key in self._seen or await self._has_seen(message.dedup_key):
                continue
            media_json = await self._download_media_parts(message.image_items)
            await self._enqueue(message, media=media_json)
            await self._mark_seen(message.dedup_key)
            self._remember_seen(message.dedup_key)
        if messages:
            _logger.info("官方 iLink 收到消息", count=len(messages))
        if sync_buf:
            self._sync_buf = sync_buf
            await self._save_sync_buf(sync_buf)
        return False

    async def _download_media_parts(
        self, image_items: tuple[dict[str, Any], ...],
    ) -> str | None:
        """逐个下载 image_item → base64 data URI；拼成 OpenAI 多模态 parts JSON 串。

        单张失败不阻塞剩下；全部失败返 None（content 仍是 "[图片]" 占位让消息能回复）。
        """
        if not image_items:
            return None
        import base64 as _b64
        import json as _json
        parts: list[dict[str, Any]] = []
        for img in image_items:
            try:
                data = await self._client.download_image(img)
            except Exception as exc:
                _logger.warning("下载 image_item 异常（跳过）", error=str(exc))
                continue
            if data is None:
                continue
            uri = "data:image/jpeg;base64," + _b64.b64encode(data).decode("ascii")
            parts.append({"type": "image_url", "image_url": {"url": uri}})
        if not parts:
            return None
        return _json.dumps(parts, ensure_ascii=False)

    async def _enqueue(
        self, message: ILinkInboundMessage, *, media: str | None = None,
    ) -> None:
        session_id = _session_id_for(message.user_id, message.group_id)
        msg_type = "image" if media else "text"
        await self._db._execute(
            """
            INSERT INTO channel_messages
              (ts, channel, direction, session_id, user_id, group_id, content, msg_type, media, processed)
            VALUES (?,?,?,?,?,?,?,?,?,0)
            """,
            (
                int(time.time() * 1000),
                "wechat",
                "in",
                session_id,
                message.user_id,
                message.group_id,
                message.text,
                msg_type,
                media,
            ),
        )

    async def _load_sync_buf(self) -> str:
        cur = await self._db._execute(
            "SELECT sync_buf FROM wechat_ilink_state WHERE account_id = ?",
            (self._account_id,),
        )
        row = cur.fetchone()
        return str(row["sync_buf"]) if row and row["sync_buf"] else ""

    async def _save_sync_buf(self, sync_buf: str) -> None:
        await self._db._execute(
            """
            INSERT INTO wechat_ilink_state (account_id, sync_buf, updated_at)
            VALUES (?,?,?)
            ON CONFLICT(account_id) DO UPDATE SET
              sync_buf = excluded.sync_buf,
              updated_at = excluded.updated_at
            """,
            (self._account_id, sync_buf, int(time.time() * 1000)),
        )

    async def _has_seen(self, dedup_key: str) -> bool:
        cur = await self._db._execute(
            "SELECT 1 FROM wechat_ilink_seen WHERE dedup_key = ? LIMIT 1",
            (dedup_key,),
        )
        return cur.fetchone() is not None

    async def _mark_seen(self, dedup_key: str) -> None:
        await self._db._execute(
            "INSERT OR IGNORE INTO wechat_ilink_seen (dedup_key, ts) VALUES (?,?)",
            (dedup_key, int(time.time() * 1000)),
        )

    def _remember_seen(self, dedup_key: str) -> None:
        self._seen.add(dedup_key)
        if len(self._seen) > 5_000:
            self._seen.clear()

    @staticmethod
    def _short(value: str) -> str:
        return value[:8] + ("..." if len(value) > 8 else "")


def _extract_sync_buf(value: Any) -> str:
    return _find_string_key(value, ("get_updates_buf", "sync_buf", "next_sync_buf"))


def _extract_inbound_messages(value: Any, account_id: str) -> list[ILinkInboundMessage]:
    messages: list[ILinkInboundMessage] = []
    _collect_message_arrays(value, account_id, messages)
    return messages


def _looks_like_inbound_payload(value: Any) -> bool:
    """启发式：响应里出现常见消息容器键 / 长字符串内容时，认为是"应该有消息但没抽出来"。"""
    if not isinstance(value, dict):
        return False
    container_keys = {"msgs", "messages", "updates", "msg_list", "items", "item_list", "list",
                      "data", "result", "events", "update_list"}
    return any(k in value and value.get(k) for k in container_keys)


def _collect_message_arrays(
    value: Any,
    account_id: str,
    messages: list[ILinkInboundMessage],
) -> None:
    if isinstance(value, list):
        for item in value:
            parsed = _parse_inbound_message(item, account_id)
            if parsed is not None:
                messages.append(parsed)
        return
    if not isinstance(value, dict):
        return

    for key in ("msgs", "messages", "updates"):
        items = value.get(key)
        if isinstance(items, list):
            for item in items:
                parsed = _parse_inbound_message(item, account_id)
                if parsed is not None:
                    messages.append(parsed)
    for key in ("data", "result"):
        child = value.get(key)
        if child is not None:
            _collect_message_arrays(child, account_id, messages)


def _parse_inbound_message(value: Any, account_id: str) -> ILinkInboundMessage | None:
    message = _message_object(value)
    if message is None:
        return None
    sender_id = _string_field(message, ("from_user_id", "from", "sender", "sender_id"))
    if not sender_id or sender_id == account_id:
        return None

    text = _extract_message_text(message)
    image_items = _extract_image_items(message)
    # 没文字也没图就丢；有图无文字时用 text="[图片]" 占位（_extract_message_text 已处理）
    if not text.strip() and not image_items:
        return None

    room_id = _string_field(message, ("room_id", "chat_room_id", "group_id"))
    peer_id = _guess_peer_id(message, account_id, sender_id)
    group_id = room_id or None
    user_id = sender_id if group_id else peer_id
    raw_message_id = _string_field(
        message,
        ("message_id", "msg_id", "client_msg_id", "new_msg_id", "id"),
    )
    timestamp = _string_field(message, ("timestamp", "msg_time", "create_time", "time", "ts"))
    if raw_message_id:
        dedup_key = f"message:{raw_message_id}"
        message_id = raw_message_id
    elif timestamp:
        dedup_key = f"content:{peer_id}:{sender_id}:{timestamp}:{_stable_hash(text)}"
        message_id = f"generated-{_stable_hash(dedup_key)}"
    else:
        message_id = f"generated-{uuid.uuid4().hex}"
        dedup_key = f"generated:{message_id}"
    return ILinkInboundMessage(
        peer_id=peer_id,
        user_id=user_id,
        group_id=group_id,
        message_id=message_id,
        dedup_key=dedup_key,
        text=text or "[图片]",  # 图片消息没文字时给占位，避免 batch 拼接出现空字符串
        image_items=tuple(image_items),
    )


def _extract_image_items(message: dict[str, Any]) -> list[dict[str, Any]]:
    """从 item_list 抽出所有 image_item（已 _ITEM_IMAGE=2 类型过滤），保留原始 dict 给下载用。"""
    out: list[dict[str, Any]] = []
    items = message.get("item_list")
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = _number_field(item, ("type",)) or 0
        image_item = item.get("image_item")
        if item_type == _ITEM_IMAGE and isinstance(image_item, dict):
            out.append(image_item)
    return out


def _message_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict) and _looks_like_message(value):
        return value
    if not isinstance(value, dict):
        return None
    for key in ("msg", "message", "payload"):
        child = value.get(key)
        if isinstance(child, dict) and _looks_like_message(child):
            return child
    return None


def _looks_like_message(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return any(key in value for key in ("item_list", "from_user_id", "message_id", "msg_id"))


def _guess_peer_id(message: dict[str, Any], account_id: str, sender_id: str) -> str:
    room_id = _string_field(message, ("room_id", "chat_room_id", "group_id"))
    to_user_id = _string_field(message, ("to_user_id",))
    msg_type = _number_field(message, ("msg_type",)) or 0
    if room_id:
        return room_id
    if to_user_id and to_user_id != account_id and msg_type == 1:
        return to_user_id
    return sender_id


def _extract_message_text(message: dict[str, Any]) -> str:
    items = message.get("item_list")
    if isinstance(items, list):
        text = _extract_text_from_items(items)
        if text:
            return text
    return _string_field(
        message,
        ("text", "content", "message", "message_content", "msg", "plain_text"),
    )


def _extract_text_from_items(items: list[Any]) -> str:
    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = _number_field(item, ("type",)) or 0
        text_item = item.get("text_item")
        if item_type == _ITEM_TEXT and isinstance(text_item, dict):
            text = _string_field(text_item, ("text",))
            if text:
                return text
    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = _number_field(item, ("type",)) or 0
        voice_item = item.get("voice_item")
        if item_type == _ITEM_VOICE and isinstance(voice_item, dict):
            text = _string_field(voice_item, ("text",))
            if text:
                return text
    # 兜底：图片/视频/文件/链接等暂不支持下载，返回占位让消息能入队
    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = _number_field(item, ("type",)) or 0
        placeholder = _NON_TEXT_PLACEHOLDERS.get(int(item_type))
        if placeholder:
            return placeholder
    return ""


def _session_id_for(wxid: str, group_id: str | None) -> str:
    if group_id:
        return f"wechat:group:{group_id}:{wxid}"
    return f"wechat:user:{wxid}"


def _find_string_key(value: Any, keys: tuple[str, ...]) -> str:
    if isinstance(value, dict):
        for key in keys:
            text = _as_string(value.get(key))
            if text:
                return text
        for child in value.values():
            text = _find_string_key(child, keys)
            if text:
                return text
    elif isinstance(value, list):
        for item in value:
            text = _find_string_key(item, keys)
            if text:
                return text
    return ""


def _string_field(value: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        text = _as_string(value.get(key))
        if text:
            return text
    return ""


def _number_field(value: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        raw = value.get(key)
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str):
            try:
                return int(raw.strip())
            except ValueError:
                continue
    return None


def _as_string(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text


def _stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
