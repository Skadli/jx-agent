"""iLink Bot REST API 异步客户端；兼容本地 webhook 网关与 Hermes 官方 Bot API。"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from typing import Any, cast
from urllib.parse import quote, urlparse

import httpx
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

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

# 图片下载走 weixin CDN；token 在 image_item.media.encrypt_query_param。
# 同时给 full_url 直连留一条路（host 白名单防 SSRF）。
# 端点和白名单参考 hermes-agent gateway/platforms/weixin.py。
WEIXIN_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
_WEIXIN_CDN_ALLOWLIST: frozenset[str] = frozenset({
    "novac2c.cdn.weixin.qq.com",
    "ilinkai.weixin.qq.com",
    "wx.qlogo.cn",
    "thirdwx.qlogo.cn",
    "res.wx.qq.com",
    "mmbiz.qpic.cn",
    "mmbiz.qlogo.cn",
})


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

    async def download_image(
        self,
        image_item: dict[str, Any],
        *,
        cdn_base_url: str = WEIXIN_CDN_BASE_URL,
        timeout_seconds: float = 30.0,
    ) -> bytes | None:
        """下载并解密微信图片消息；失败返 None 不抛（让 poller 退化成占位文本）。

        image_item 来自 iLink item_list[*].image_item，常见字段：
        - aeskey：32 字符 hex，AES-128 key
        - media.encrypt_query_param：加密下载 token（推荐路径）
        - media.full_url：直连 CDN URL（fallback，需白名单）
        协议同 hermes-agent gateway/platforms/weixin.py。
        """
        media = image_item.get("media") if isinstance(image_item.get("media"), dict) else {}
        encrypt_token = media.get("encrypt_query_param") if isinstance(media, dict) else None
        full_url = media.get("full_url") if isinstance(media, dict) else None
        aeskey = image_item.get("aeskey")

        try:
            if encrypt_token:
                url = f"{cdn_base_url.rstrip('/')}/download?encrypted_query_param={quote(str(encrypt_token), safe='')}"
            elif full_url:
                _assert_weixin_cdn_url(str(full_url))
                url = str(full_url)
            else:
                _logger.warning("image_item 缺 encrypt_query_param 和 full_url；放弃下载")
                return None
            r = await self._client.get(url, timeout=timeout_seconds)
            r.raise_for_status()
            raw = r.content
            if aeskey:
                key = _parse_aes_key(str(aeskey))
                raw = _aes128_ecb_decrypt(raw, key)
            return raw
        except (httpx.HTTPError, ValueError, OSError) as exc:
            _logger.warning(
                "微信图片下载/解密失败",
                error=f"{type(exc).__name__}: {exc}",
                has_token=bool(encrypt_token),
                has_full_url=bool(full_url),
            )
            return None

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


def _parse_aes_key(raw: str) -> bytes:
    """image_item.aeskey 可能是 32 字符 hex（最常见），也可能是 base64 编码的 16 字节。

    hermes-agent _parse_aes_key 同源逻辑：先 base64 解，长度 16 直接用；长度 32
    再尝试当 hex ascii 用。
    """
    try:
        decoded = base64.b64decode(raw, validate=False)
    except (ValueError, TypeError):
        decoded = b""
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        text = decoded.decode("ascii", errors="ignore")
        if text and all(c in "0123456789abcdefABCDEF" for c in text):
            return bytes.fromhex(text)
    # base64 失败时直接当 hex 试一次
    raw_str = raw.strip()
    if len(raw_str) == 32 and all(c in "0123456789abcdefABCDEF" for c in raw_str):
        return bytes.fromhex(raw_str)
    raise ValueError(f"aeskey 格式不识别（base64 解码 {len(decoded)} 字节，raw {len(raw_str)} 字符）")


def _aes128_ecb_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    """AES-128-ECB 解密 + PKCS7 padding 手工剥；密文长度非 16 倍数也直接抛 ValueError。"""
    if len(ciphertext) % 16 != 0:
        raise ValueError(f"密文长度 {len(ciphertext)} 不是 16 的倍数；AES-ECB 解密失败")
    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    if not padded:
        return padded
    pad_len = padded[-1]
    if 1 <= pad_len <= 16 and padded.endswith(bytes([pad_len]) * pad_len):
        return padded[:-pad_len]
    return padded


def _assert_weixin_cdn_url(url: str) -> None:
    """SSRF guard：图片 full_url 必须落在已知 weixin CDN host 白名单内。"""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    host = parsed.hostname or ""
    if scheme not in {"http", "https"}:
        raise ValueError(f"image full_url scheme={scheme!r} 非 http/https，拒绝下载")
    if host not in _WEIXIN_CDN_ALLOWLIST:
        raise ValueError(f"image full_url host={host!r} 不在白名单，拒绝下载（防 SSRF）")


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
