"""Dashboard password gate for local web console APIs."""

from __future__ import annotations

import hmac
import json
import secrets
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
    """Process-local token auth backed by a password from env."""

    def __init__(self, password: SecretStr | str | None) -> None:
        self._password = _secret_value(password)
        self._token = secrets.token_urlsafe(32)

    @property
    def configured(self) -> bool:
        return bool(self._password)

    def check_password(self, password: str) -> bool:
        return self.configured and hmac.compare_digest(password, self._password)

    def issue_token(self) -> str:
        self._token = secrets.token_urlsafe(32)
        return self._token

    def invalidate(self) -> None:
        self._token = secrets.token_urlsafe(32)

    def authorized(self, headers: Any) -> bool:
        if not self.configured:
            return True
        token = headers.get("X-Dashboard-Token") or headers.get("x-dashboard-token") or ""
        return bool(token) and hmac.compare_digest(str(token), self._token)


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
        auth.invalidate()
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
