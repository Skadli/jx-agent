"""iLink Bot REST API 异步客户端；兼容本地 webhook 网关与 Hermes 官方 Bot API。"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from typing import Any, cast

import httpx

from sanshiliu.foundation.errors import ChannelError
from sanshiliu.foundation.logging import get_logger

_logger = get_logger(__name__)

_CHANNEL_VERSION = "2.2.0"
_ILINK_APP_ID = "bot"
_ILINK_APP_CLIENT_VERSION = str((2 << 16) | (2 << 8))
_EP_GET_UPDATES = "ilink/bot/getupdates"
_EP_SEND_MESSAGE = "ilink/bot/sendmessage"
_ITEM_TEXT = 1
_MSG_TYPE_BOT = 2
_MSG_STATE_FINISH = 2

# iLink session-expired 信号；poller 看到这个 errcode 就退避并通知 health
ILINK_ERR_SESSION_TIMEOUT = -14


class ILinkClient:
    """httpx.AsyncClient 包装；官方模式需要 account_id + token。"""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        *,
        account_id: str = "",
        user_id: str = "",
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._account_id = account_id.strip()
        self._user_id = user_id.strip()
        self._official = bool(self._account_id and self._api_key)
        # X-WECHAT-UIN 必须跨请求稳定（之前每次随机 → iLink 视为新 session，token -14）
        self._wechat_uin = _stable_wechat_uin(self._user_id or self._account_id)
        headers = {"User-Agent": "sanshiliu/1.0"}
        if api_key and not self._official:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(base_url=self._base_url, headers=headers, timeout=timeout)

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def official(self) -> bool:
        return self._official

    @property
    def account_id(self) -> str:
        return self._account_id

    async def close(self) -> None:
        await self._client.aclose()

    async def ping(self) -> bool:
        """轻量探活；返回 True 表示 iLink 可达。"""
        if self._official:
            # 官方 iLink 没有无副作用 ping；长轮询任务会负责把健康状态置 up/down。
            return True
        try:
            r = await self._client.get("/ping")
            return r.status_code < 500
        except (httpx.HTTPError, OSError):
            return False

    async def send_text(self, to_wxid: str, text: str) -> dict[str, Any]:
        """发文本消息；失败抛 ChannelError。"""
        if self._official:
            return await self._send_official_text(to_wxid, text)
        try:
            r = await self._client.post("/send_text", json={"to_wxid": to_wxid, "content": text})
        except httpx.HTTPError as exc:
            raise ChannelError(f"iLink /send_text 调用失败：{exc}") from exc
        if r.status_code >= 400:
            raise ChannelError(f"iLink /send_text 返回 {r.status_code}: {r.text[:200]}")
        if not r.headers.get("content-type", "").startswith("application/json"):
            return {}
        parsed = r.json()
        return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else {}

    async def get_self_info(self) -> dict[str, Any]:
        """拿 bot 自己的 wxid / 昵称；启动期一次性。"""
        if self._official:
            return {"wxid": self._account_id, "account_id": self._account_id}
        try:
            r = await self._client.get("/get_self_info")
        except httpx.HTTPError as exc:
            raise ChannelError(f"iLink /get_self_info 调用失败：{exc}") from exc
        if r.status_code >= 400:
            raise ChannelError(f"iLink /get_self_info 返回 {r.status_code}")
        parsed = r.json()
        return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else {}

    async def get_updates(self, sync_buf: str, *, timeout_ms: int) -> dict[str, Any]:
        """官方 iLink Bot 长轮询拉取消息；仅 official 模式可用。"""
        if not self._official:
            raise ChannelError("本地 iLink 模式不支持 getupdates")
        return await self._official_post(
            _EP_GET_UPDATES,
            {"get_updates_buf": sync_buf},
            timeout_ms=timeout_ms,
        )

    async def _send_official_text(self, to_user_id: str, text: str) -> dict[str, Any]:
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": f"sanshiliu-{uuid.uuid4().hex}",
                "message_type": _MSG_TYPE_BOT,
                "message_state": _MSG_STATE_FINISH,
                "item_list": [
                    {"type": _ITEM_TEXT, "text_item": {"text": text}},
                ],
            },
        }
        response = await self._official_post(_EP_SEND_MESSAGE, payload, timeout_ms=15_000)
        code = _number_field(response, ("errcode", "ret"))
        if code not in (None, 0):
            message = _string_field(response, ("errmsg", "message")) or str(response)
            raise ChannelError(f"iLink sendmessage 返回 {code}: {message}")
        return response

    async def _official_post(
        self,
        endpoint: str,
        payload: dict[str, Any],
        *,
        timeout_ms: int,
    ) -> dict[str, Any]:
        if not self._api_key:
            raise ChannelError("iLink 官方模式缺少 token")
        body = dict(payload)
        body["base_info"] = {"channel_version": _CHANNEL_VERSION}
        body_bytes = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = _official_headers(self._api_key, len(body_bytes), self._wechat_uin)
        try:
            response = await self._client.post(
                f"/{endpoint}",
                content=body_bytes,
                headers=headers,
                timeout=timeout_ms / 1000,
            )
        except httpx.HTTPError as exc:
            raise ChannelError(f"iLink {endpoint} 调用失败：{exc}") from exc
        raw = response.text
        if response.status_code >= 400:
            raise ChannelError(f"iLink {endpoint} 返回 {response.status_code}: {raw[:200]}")
        try:
            parsed = response.json()
        except json.JSONDecodeError as exc:
            raise ChannelError(f"iLink {endpoint} 返回非 JSON：{raw[:200]}") from exc
        if not isinstance(parsed, dict):
            raise ChannelError(f"iLink {endpoint} 返回非对象 JSON")
        return parsed


def _official_headers(token: str, content_length: int, wechat_uin: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Length": str(content_length),
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": wechat_uin,
        "iLink-App-Id": _ILINK_APP_ID,
        "iLink-App-ClientVersion": _ILINK_APP_CLIENT_VERSION,
        "Authorization": f"Bearer {token}",
    }


def _stable_wechat_uin(seed: str) -> str:
    """从 account_id/user_id 生成稳定 32 位 UIN（base64 of digit string）。
    iLink 用 X-WECHAT-UIN 关联 session；每次随机会被服务端视作不同客户端连接，
    最终触发 errcode=-14 session timeout。
    """
    base = seed.strip() or "anonymous"
    digest = hashlib.sha256(base.encode("utf-8")).digest()
    value = int.from_bytes(digest[:4], "big") & 0xFFFF_FFFF
    return base64.b64encode(str(value).encode("utf-8")).decode("ascii")


# 兼容旧调用点：保留旧函数名指向稳定版本（uuid 仍 import 用作 fallback）
def _random_wechat_uin() -> str:
    return _stable_wechat_uin(uuid.NAMESPACE_OID.hex)


def _string_field(value: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        raw = value.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
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
