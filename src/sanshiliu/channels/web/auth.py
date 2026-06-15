"""Dashboard password gate for local web console APIs."""

from __future__ import annotations

import hmac
import json
import secrets
import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler


def _secret_value(value: SecretStr | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return str(value)


class DashboardAuth:
    """Process-local token auth backed by a password from env.

    多会话并存：每次登录签发一枚独立 token 存进集合，互不挤占——多个用户 / 多个浏览器标签页
    可同时在线（修复"新登录把旧会话挤掉"）。token 是 256-bit 随机串；登出只注销本会话那一枚。
    """

    # 防无界增长：在线会话 token 上限，超出按签发顺序（FIFO）淘汰最早的一枚
    _MAX_TOKENS = 64

    def __init__(self, password: SecretStr | str | None) -> None:
        self._password = _secret_value(password)
        # token → None；OrderedDict 仅借其插入序做 FIFO 淘汰
        self._tokens: OrderedDict[str, None] = OrderedDict()
        # ThreadingHTTPServer 每请求一线程：登录(写) 与 鉴权(读) 会并发，加锁防迭代时改动
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._password)

    def check_password(self, password: str) -> bool:
        return self.configured and hmac.compare_digest(password, self._password)

    def issue_token(self) -> str:
        """签发并登记一枚新 token；不动已签发的其它 token（旧会话继续有效）。"""
        tok = secrets.token_urlsafe(32)
        with self._lock:
            self._tokens[tok] = None
            while len(self._tokens) > self._MAX_TOKENS:
                self._tokens.popitem(last=False)  # FIFO：淘汰最早签发的
        return tok

    def revoke(self, token: str) -> None:
        """注销单枚 token（登出当前会话）；其它在线会话不受影响。"""
        if not token:
            return
        with self._lock:
            self._tokens.pop(token, None)

    def authorized(self, headers: Any) -> bool:
        if not self.configured:
            return True
        token = headers.get("X-Dashboard-Token") or headers.get("x-dashboard-token") or ""
        if not token:
            return False
        token = str(token)
        with self._lock:
            candidates = tuple(self._tokens.keys())
        # token 为高熵随机串；逐枚常量时间比对，命中任意一枚即放行
        return any(hmac.compare_digest(token, t) for t in candidates)


def write_auth_error(req: BaseHTTPRequestHandler) -> None:
    body = json.dumps({"error": "unauthorized"}, ensure_ascii=False).encode("utf-8")
    req.send_response(401)
    req.send_header("Content-Type", "application/json; charset=utf-8")
    req.send_header("Content-Length", str(len(body)))
    req.send_header("Cache-Control", "no-store")
    req.end_headers()
    req.wfile.write(body)


def make_auth_status_handler(auth: DashboardAuth) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        payload = {
            "configured": auth.configured,
            "authenticated": auth.authorized(req.headers),
        }
        _write_json(req, payload)

    return handler


def make_auth_login_handler(auth: DashboardAuth) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        body = _read_json(req) or {}
        password = str(body.get("password") or "")
        if not auth.configured:
            _write_json(req, {"ok": True, "configured": False, "token": ""})
            return
        if not auth.check_password(password):
            _write_json(req, {"error": "password incorrect"}, status=401)
            return
        _write_json(req, {"ok": True, "configured": True, "token": auth.issue_token()})

    return handler


def make_auth_logout_handler(auth: DashboardAuth) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        # 只注销当前会话那枚 token，不波及其它在线用户 / 标签页
        token = req.headers.get("X-Dashboard-Token") or req.headers.get("x-dashboard-token") or ""
        auth.revoke(str(token))
        _write_json(req, {"ok": True})

    return handler


def _read_json(req: BaseHTTPRequestHandler) -> dict[str, Any] | None:
    length = int(req.headers.get("Content-Length", "0") or "0")
    if length <= 0 or length > 64 * 1024:
        return None
    try:
        data = json.loads(req.rfile.read(length).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(req: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req.send_response(status)
    req.send_header("Content-Type", "application/json; charset=utf-8")
    req.send_header("Content-Length", str(len(body)))
    req.send_header("Cache-Control", "no-store")
    req.end_headers()
    req.wfile.write(body)
