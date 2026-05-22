"""webhook HMAC 校验 + 入队单测（V-8 重点）。"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio

from sanshiliu.channels.wechat.webhook import WechatWebhookProcessor, verify_hmac
from sanshiliu.storage.db import Database


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    d = Database(tmp_path / "t.db")
    await d.connect()
    yield d
    await d.close()


def test_verify_hmac_correct() -> None:
    assert verify_hmac("secret", b"hello", _sign("secret", b"hello")) is True


def test_verify_hmac_wrong_signature() -> None:
    assert verify_hmac("secret", b"hello", "deadbeef") is False


def test_verify_hmac_constant_time_against_empty() -> None:
    assert verify_hmac("secret", b"hello", "") is False


async def test_processor_rejects_bad_signature_returns_401(db: Database) -> None:
    """V-8：错误 HMAC → 401 + 安全日志。"""
    p = WechatWebhookProcessor(db=db, webhook_secret="s1")
    status, msg = await p.process(b'{"from_wxid":"w1","content":"hi"}', {"X-iLink-Signature": "wrong"})
    assert status == 401
    assert "invalid" in msg.lower()


async def test_processor_accepts_correct_signature_and_enqueues(db: Database) -> None:
    p = WechatWebhookProcessor(db=db, webhook_secret="s1")
    body = json.dumps({"from_wxid": "w1", "content": "你好", "msg_type": "text"}).encode("utf-8")
    sig = _sign("s1", body)
    status, msg = await p.process(body, {"X-iLink-Signature": sig})
    assert status == 200
    cur = await db._execute(  # noqa: SLF001
        "SELECT user_id, content, direction, processed FROM channel_messages WHERE channel='wechat'"
    )
    rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["user_id"] == "w1"
    assert rows[0]["content"] == "你好"
    assert rows[0]["direction"] == "in"
    assert rows[0]["processed"] == 0


async def test_processor_rejects_invalid_json(db: Database) -> None:
    p = WechatWebhookProcessor(db=db, webhook_secret="s1")
    body = b"not json"
    sig = _sign("s1", body)
    status, _ = await p.process(body, {"X-iLink-Signature": sig})
    assert status == 400


async def test_processor_rejects_missing_fields(db: Database) -> None:
    p = WechatWebhookProcessor(db=db, webhook_secret="s1")
    body = json.dumps({"foo": "bar"}).encode("utf-8")
    sig = _sign("s1", body)
    status, _ = await p.process(body, {"X-iLink-Signature": sig})
    assert status == 400


async def test_processor_header_case_insensitive(db: Database) -> None:
    """实际 http 请求的 header key 大小写不固定；小写也能识别。"""
    p = WechatWebhookProcessor(db=db, webhook_secret="s1")
    body = json.dumps({"from_wxid": "w1", "content": "x"}).encode("utf-8")
    sig = _sign("s1", body)
    status, _ = await p.process(body, {"x-ilink-signature": sig})
    assert status == 200
