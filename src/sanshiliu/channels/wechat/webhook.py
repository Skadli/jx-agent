"""iLink webhook 接收 + HMAC 校验 + 入队。"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

from sanshiliu.foundation.logging import get_logger
from sanshiliu.storage.db import Database

_logger = get_logger(__name__)

# HMAC 签名 header 名；与 iLink 文档约定，可在 settings 中覆盖
_DEFAULT_SIGNATURE_HEADER = "X-iLink-Signature"


def verify_hmac(secret: str, body: bytes, signature: str) -> bool:
    """常数时间比对；签名为 hex(sha256(secret, body))。"""
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


class WechatWebhookProcessor:
    """处理一条 webhook 请求；HMAC 错误 → 401 安全日志；成功 → 入 channel_messages。"""

    def __init__(
        self,
        *,
        db: Database,
        webhook_secret: str,
        signature_header: str = _DEFAULT_SIGNATURE_HEADER,
    ) -> None:
        self._db = db
        self._secret = webhook_secret
        self._sig_header = signature_header

    async def process(self, body: bytes, headers: dict[str, str]) -> tuple[int, str]:
        """处理一条 webhook；返回 (HTTP 状态码, 响应文本)。"""
        sig = headers.get(self._sig_header) or headers.get(self._sig_header.lower()) or ""
        if not sig or not verify_hmac(self._secret, body, sig):
            _logger.warning(
                "webhook HMAC 校验失败",
                received_sig=sig[:16],
                body_size=len(body),
            )
            return 401, "invalid signature"

        try:
            payload: dict[str, Any] = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            _logger.warning("webhook 包体非法 JSON", error=str(exc))
            return 400, "invalid json"

        # iLink 上行消息格式：{type, from_wxid, group_id?, content, msg_type, image_url?, media?}
        msg_type = str(payload.get("msg_type") or payload.get("type") or "text")
        from_wxid = str(payload.get("from_wxid") or payload.get("user_id") or "")
        group_id = payload.get("group_id")
        content = str(payload.get("content") or "")

        # Phase 10：image 消息允许 content 为空，但 image_url / media 必填
        media_json = _extract_media(payload, msg_type)
        if not from_wxid:
            return 400, "missing from_wxid"
        if not content and media_json is None:
            return 400, "missing content or media"

        # 入队：写一行 channel_messages，processed=0 等 bot 拉
        session_id = _session_id_for(from_wxid, group_id)
        try:
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
                    from_wxid,
                    group_id,
                    content,
                    msg_type,
                    media_json,
                ),
            )
        except Exception as exc:
            _logger.error("webhook 入队失败", error=str(exc))
            return 500, "queue write failed"

        return 200, "ok"


def _session_id_for(wxid: str, group_id: str | None) -> str:
    """每个 wxid + group_id 一个独立会话。"""
    if group_id:
        return f"wechat:group:{group_id}:{wxid}"
    return f"wechat:user:{wxid}"


def _extract_media(payload: dict[str, Any], msg_type: str) -> str | None:
    """Phase 10：从 iLink payload 抽出图片/媒体描述，序列化成 JSON 存 channel_messages.media。

    iLink 历史 payload 兼容多个字段名：
    - `image_url` / `media_url`：单图 URL
    - `media`：list[dict]，每条 {type, url}
    返回 None 表示纯文本。
    """
    if msg_type in ("text",) and "media" not in payload and "image_url" not in payload:
        return None
    parts: list[dict[str, Any]] = []
    raw_media = payload.get("media")
    if isinstance(raw_media, list):
        for item in raw_media:
            if not isinstance(item, dict):
                continue
            url = item.get("url") or item.get("image_url")
            if isinstance(url, str) and url:
                parts.append({"type": "image_url", "image_url": {"url": url}})
    for key in ("image_url", "media_url"):
        url = payload.get(key)
        if isinstance(url, str) and url:
            parts.append({"type": "image_url", "image_url": {"url": url}})
    if not parts:
        return None
    return json.dumps(parts, ensure_ascii=False)
