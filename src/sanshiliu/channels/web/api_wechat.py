"""dashboard 微信扫码连接端点；复用 bootstrap.wechat_setup 里的 iLink QR 流程。

设计：
- WechatQrBroker 持有 asyncio loop，每次 start 开一个后台 task 轮询 iLink 状态；
- 前端短轮询 /status，根据 status 切换 UI；
- confirmed 后自动落 wechat-account.json 并把 4 个 env key 写入 .env；
- 扫码成功 ≠ wechat bot 已运行 —— 仍需提示用户重启进程以加载新配置。
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from sanshiliu.bootstrap.wechat_setup import (
    DEFAULT_QR_BOT_TYPE,
    DEFAULT_QR_TIMEOUT_SECONDS,
    EP_GET_BOT_QR,
    EP_GET_QR_STATUS,
    ILINK_BASE_URL,
    QrLoginCode,
    WechatCredentials,
    _credentials_from_confirmed_status,
    _env_updates_for_credentials,
    _ilink_get,
    _ilink_get_headers,
    _string_field,
    save_wechat_credentials,
)
from sanshiliu.channels.web.api_settings import _write_env_file
from sanshiliu.foundation.logging import get_logger

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler

_logger = get_logger(__name__)

_QR_HTTP_TIMEOUT_SECONDS = 40.0
_MAX_QR_REFRESHES = 3
_SESSION_TTL_SECONDS = DEFAULT_QR_TIMEOUT_SECONDS + 60  # 给前端一点延迟


def _qr_svg_data_url(data: str) -> str:
    """把 scan_data 渲染为 inline SVG 的 base64 data URL，前端 <img src=...> 直接用。"""
    try:
        import qrcode
        from qrcode.image.svg import SvgImage
    except ImportError as exc:
        raise RuntimeError("缺少 qrcode 依赖") from exc

    qr = qrcode.QRCode(border=2, image_factory=SvgImage)
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image()
    buf = io.BytesIO()
    image.save(buf)
    body = buf.getvalue()
    return "data:image/svg+xml;base64," + base64.b64encode(body).decode("ascii")


class WechatQrBroker:
    """单例：跨 HTTP 请求记录当前正在跑的 QR 登录会话。"""

    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        env_path: Path,
        store_path: Path,
    ) -> None:
        self._loop = loop
        self._env_path = env_path
        self._store_path = store_path
        self._sessions: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._lock = threading.Lock()

    # ── 外部接口（HTTP handler 用） ──

    def start(self) -> dict[str, Any]:
        """阻塞地在 loop 上跑一次 start_async，返回初始 state。"""
        fut = asyncio.run_coroutine_threadsafe(self._start_async(), self._loop)
        return fut.result(timeout=30.0)

    def get_status(self, session_id: str) -> dict[str, Any] | None:
        self._gc_expired()
        with self._lock:
            s = self._sessions.get(session_id)
            if not s:
                return None
            return _snapshot(s)

    def cancel(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(session_id, None)
            task = self._tasks.pop(session_id, None)
        if task and not task.done():
            self._loop.call_soon_threadsafe(task.cancel)
        return session is not None

    # ── 内部 ──

    async def _start_async(self) -> dict[str, Any]:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(_QR_HTTP_TIMEOUT_SECONDS, connect=10.0)
        )
        try:
            qr_code = await _fetch_qr(client, bot_type=DEFAULT_QR_BOT_TYPE)
        except Exception:
            await client.aclose()
            raise

        session_id = uuid.uuid4().hex
        deadline = time.monotonic() + DEFAULT_QR_TIMEOUT_SECONDS
        state: dict[str, Any] = {
            "session_id": session_id,
            "status": "wait",
            "status_label": _status_label("wait"),
            "scan_data": qr_code.scan_data,
            "qr_data_url": _qr_svg_data_url(qr_code.scan_data),
            "qrcode": qr_code.qrcode,
            "credentials": None,
            "applied_env": None,
            "error": None,
            "created_at": time.time(),
            "deadline_monotonic": deadline,
            "expires_at": time.time() + _SESSION_TTL_SECONDS,
        }
        with self._lock:
            self._sessions[session_id] = state

        task = asyncio.create_task(self._poll_loop(session_id, client, qr_code))
        self._tasks[session_id] = task
        return _snapshot(state)

    async def _poll_loop(
        self,
        session_id: str,
        client: httpx.AsyncClient,
        qr_code: QrLoginCode,
    ) -> None:
        current_base_url = ILINK_BASE_URL
        refresh_count = 0
        last_status = ""
        try:
            while True:
                with self._lock:
                    state = self._sessions.get(session_id)
                    if state is None:
                        return
                    deadline = state["deadline_monotonic"]
                if time.monotonic() > deadline:
                    self._mark(session_id, status="timeout", error="QR 登录超时")
                    return

                try:
                    resp = await _ilink_get(
                        client, current_base_url, EP_GET_QR_STATUS,
                        params={"qrcode": qr_code.qrcode},
                    )
                except httpx.HTTPError as exc:
                    _logger.warning("QR 状态轮询失败", error=str(exc))
                    await asyncio.sleep(1.5)
                    continue

                status = _string_field(resp, ("status",)) or "wait"
                if status != last_status:
                    _logger.info("wechat QR 状态", session=session_id, status=status)
                    last_status = status

                if status in ("wait", "scaned"):
                    self._mark(session_id, status=status)
                elif status == "scaned_but_redirect":
                    self._mark(session_id, status=status)
                    redirect_host = _string_field(resp, ("redirect_host",))
                    if redirect_host:
                        current_base_url = f"https://{redirect_host.rstrip('/')}"
                elif status == "expired":
                    refresh_count += 1
                    if refresh_count > _MAX_QR_REFRESHES:
                        self._mark(session_id, status="expired", error="二维码刷新次数过多")
                        return
                    # 刷一张新 QR，更新 state
                    try:
                        new_qr = await _fetch_qr(client, bot_type=DEFAULT_QR_BOT_TYPE)
                    except Exception as exc:
                        self._mark(session_id, status="error", error=f"刷新二维码失败：{exc}")
                        return
                    qr_code = new_qr
                    current_base_url = ILINK_BASE_URL
                    with self._lock:
                        state = self._sessions.get(session_id)
                        if state is None:
                            return
                        state["status"] = "wait"
                        state["status_label"] = _status_label("wait")
                        state["scan_data"] = new_qr.scan_data
                        state["qr_data_url"] = _qr_svg_data_url(new_qr.scan_data)
                        state["qrcode"] = new_qr.qrcode
                        state["deadline_monotonic"] = time.monotonic() + DEFAULT_QR_TIMEOUT_SECONDS
                        state["expires_at"] = time.time() + _SESSION_TTL_SECONDS
                elif status == "confirmed":
                    try:
                        creds = _credentials_from_confirmed_status(resp)
                        save_wechat_credentials(self._store_path, creds)
                        updates = _env_updates_for_credentials(creds, self._store_path)
                        _write_env_file(self._env_path, updates)
                    except Exception as exc:
                        _logger.exception("写入扫码凭据失败", error=str(exc))
                        self._mark(session_id, status="error", error=f"写入失败：{exc}")
                        return
                    with self._lock:
                        state = self._sessions.get(session_id)
                        if state is not None:
                            state["status"] = "confirmed"
                            state["status_label"] = _status_label("confirmed")
                            state["credentials"] = {
                                "account_id": creds.account_id,
                                "base_url":   creds.base_url,
                                "user_id":    creds.user_id,
                            }
                            state["applied_env"] = list(updates.keys())
                    return
                else:
                    self._mark(session_id, status=status)

                await asyncio.sleep(1.5)
        except asyncio.CancelledError:
            self._mark(session_id, status="cancelled")
        finally:
            with self._lock:
                self._tasks.pop(session_id, None)
            await client.aclose()

    def _mark(self, session_id: str, *, status: str, error: str | None = None) -> None:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return
            state["status"] = status
            state["status_label"] = _status_label(status)
            if error is not None:
                state["error"] = error

    def _gc_expired(self) -> None:
        now = time.time()
        with self._lock:
            to_drop = [k for k, v in self._sessions.items() if v["expires_at"] < now]
            for k in to_drop:
                self._sessions.pop(k, None)
                t = self._tasks.pop(k, None)
                if t and not t.done():
                    self._loop.call_soon_threadsafe(t.cancel)


def _snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """复制出可序列化的 state，去掉内部字段。"""
    return {
        "session_id":     state["session_id"],
        "status":         state["status"],
        "status_label":   state["status_label"],
        "qr_data_url":    state["qr_data_url"],
        "scan_data":      state.get("scan_data"),
        "credentials":    state.get("credentials"),
        "applied_env":    state.get("applied_env"),
        "error":          state.get("error"),
        "expires_in":     max(0, int(state["deadline_monotonic"] - time.monotonic())),
    }


def _status_label(status: str) -> str:
    return {
        "wait":                "等待扫码",
        "scaned":              "已扫码，等待手机确认",
        "scaned_but_redirect": "已扫码，正在切换服务地址",
        "confirmed":           "已确认，凭据已写入 .env",
        "expired":             "二维码已过期",
        "timeout":             "登录超时",
        "cancelled":           "已取消",
        "error":               "出错",
    }.get(status, status)


async def _fetch_qr(client: httpx.AsyncClient, *, bot_type: str) -> QrLoginCode:
    response = await _ilink_get(
        client, ILINK_BASE_URL, EP_GET_BOT_QR,
        params={"bot_type": bot_type},
    )
    qrcode = _string_field(response, ("qrcode",))
    if not qrcode:
        raise ValueError("iLink QR 响应缺少 qrcode 字段")
    scan_data = _string_field(response, ("qrcode_img_content",)) or qrcode
    return QrLoginCode(qrcode=qrcode, scan_data=scan_data)


# ────────── HTTP handlers ──────────

def _read_json(req: BaseHTTPRequestHandler) -> dict[str, Any] | None:
    length = int(req.headers.get("Content-Length", "0") or "0")
    if length <= 0 or length > 64 * 1024:
        return {}
    try:
        parsed = json.loads(req.rfile.read(length).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _write_json(req: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    req.send_response(status)
    req.send_header("Content-Type", "application/json; charset=utf-8")
    req.send_header("Content-Length", str(len(body)))
    req.end_headers()
    req.wfile.write(body)


def make_wechat_qr_start_handler(
    broker: WechatQrBroker,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        body = _read_json(req)
        if body is None:
            _write_json(req, {"error": "invalid JSON"}, status=400); return
        try:
            state = broker.start()
        except Exception as exc:
            _logger.exception("启动 wechat QR 失败", error=str(exc))
            _write_json(req, {"error": str(exc)}, status=500); return
        _write_json(req, state)

    return handler


def make_wechat_qr_status_handler(
    broker: WechatQrBroker,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        # /api/wechat/qr/status?session=...
        path = req.path
        sid = ""
        if "?" in path:
            from urllib.parse import parse_qs
            qs = parse_qs(path.split("?", 1)[1])
            sid = (qs.get("session") or qs.get("id") or [""])[0]
        if not sid:
            _write_json(req, {"error": "missing session id"}, status=400); return
        state = broker.get_status(sid)
        if state is None:
            _write_json(req, {"error": "not found or expired", "status": "expired"}, status=404); return
        _write_json(req, state)

    return handler


def make_wechat_qr_cancel_handler(
    broker: WechatQrBroker,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        body = _read_json(req) or {}
        sid = str(body.get("session_id") or "").strip()
        if not sid:
            _write_json(req, {"error": "missing session_id"}, status=400); return
        ok = broker.cancel(sid)
        _write_json(req, {"ok": ok})

    return handler
