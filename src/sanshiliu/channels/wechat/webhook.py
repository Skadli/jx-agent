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

        # iLink 上行消息格式：{type, from_wxid, group_id?, content, msg_type}
        msg_type = str(payload.get("msg_type") or payload.get("type") or "text")
        from_wxid = str(payload.get("from_wxid") or payload.get("user_id") or "")
        group_id = payload.get("group_id")
        content = str(payload.get("content") or "")

        if not from_wxid or not content:
            return 400, "missing from_wxid or content"

        # 入队：写一行 channel_messages，processed=0 等 bot 拉
        session_id = _session_id_for(from_wxid, group_id)
        try:
            await self._db._execute(  # noqa: SLF001 - 内部友元访问
                """
                INSERT INTO channel_messages
                  (ts, channel, direction, session_id, user_id, group_id, content, msg_type, processed)
                VALUES (?,?,?,?,?,?,?,?,0)
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
