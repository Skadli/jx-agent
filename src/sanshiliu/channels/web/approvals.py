"""Web chat tool approval bridge.

PermissionManager runs inside the engine loop, while the browser can only answer
over a separate HTTP request. This broker sends approval prompts over the active
/chat SSE stream and resolves them from POST /api/tool_approvals/{id}.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import threading
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sanshiliu.channels.web.responses import write_json as _write_json
from sanshiliu.foundation.logging import get_logger
from sanshiliu.security.types import ConfirmRequest, ConfirmResponse

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler

_logger = get_logger(__name__)

_APPROVAL_TIMEOUT_SEC = 90.0
_current_emitter: contextvars.ContextVar[Callable[[dict[str, Any]], None] | None] = (
    contextvars.ContextVar("web_tool_approval_emitter", default=None)
)


class WebApprovalBroker:
    """Coordinates one-shot tool approvals between an SSE chat and POST replies."""

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[ConfirmResponse]] = {}
        self._lock = threading.Lock()

    def bind_emitter(
        self,
        emitter: Callable[[dict[str, Any]], None],
    ) -> contextvars.Token[Callable[[dict[str, Any]], None] | None]:
        return _current_emitter.set(emitter)

    def reset_emitter(
        self,
        token: contextvars.Token[Callable[[dict[str, Any]], None] | None],
    ) -> None:
        _current_emitter.reset(token)

    async def request(self, request: ConfirmRequest) -> ConfirmResponse:
        emitter = _current_emitter.get()
        if emitter is None:
            _logger.info("web 工具审批无活跃 SSE，上下文按拒绝处理", tool=request.tool_name)
            return ConfirmResponse(decision="deny", scope="once")

        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ConfirmResponse] = loop.create_future()
        with self._lock:
            self._pending[request_id] = future

        payload = {
            "id": request_id,
            "tool_name": request.tool_name,
            "canonical_name": request.canonical_name,
            "arguments_preview": request.arguments_preview,
            "danger": request.danger,
            "matched_rule": request.matched_rule,
            "timeout_sec": _APPROVAL_TIMEOUT_SEC,
        }
        emitter(payload)

        try:
            return await asyncio.wait_for(future, timeout=_APPROVAL_TIMEOUT_SEC)
        except TimeoutError:
            _logger.info("web 工具审批超时，按拒绝处理", tool=request.tool_name)
            return ConfirmResponse(decision="deny", scope="once")
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    def resolve(self, request_id: str, response: ConfirmResponse) -> bool:
        with self._lock:
            future = self._pending.get(request_id)
        if future is None:
            return False

        def _set_result() -> None:
            if not future.done():
                future.set_result(response)

        future.get_loop().call_soon_threadsafe(_set_result)
        return True


class WebApprovalConfirmer:
    """Confirmer implementation used by web chat requests."""

    def __init__(self, broker: WebApprovalBroker) -> None:
        self._broker = broker

    async def confirm(self, request: ConfirmRequest) -> ConfirmResponse:
        return await self._broker.request(request)


def make_tool_approval_handler(
    broker: WebApprovalBroker,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        raw = req.path.split("?", 1)[0]
        prefix = "/api/tool_approvals/"
        if not raw.startswith(prefix):
            _write_json(req, {"error": "bad path"}, status=400)
            return
        request_id = raw[len(prefix):].strip("/")
        if not request_id or "/" in request_id:
            _write_json(req, {"error": "bad path"}, status=400)
            return

        body = _read_json(req)
        if body is None:
            _write_json(req, {"error": "invalid JSON"}, status=400)
            return
        decision = str(body.get("decision") or "")
        scope = str(body.get("scope") or "once")
        if decision not in ("allow", "deny"):
            _write_json(req, {"error": "decision must be allow/deny"}, status=400)
            return
        if scope not in ("once", "session", "permanent"):
            _write_json(req, {"error": "scope must be once/session/permanent"}, status=400)
            return

        response = ConfirmResponse(
            decision=decision,  # type: ignore[arg-type]
            scope=scope,  # type: ignore[arg-type]
        )
        if not broker.resolve(request_id, response):
            _write_json(req, {"error": "approval not found or expired"}, status=404)
            return
        _write_json(req, {"ok": True, "id": request_id})

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
