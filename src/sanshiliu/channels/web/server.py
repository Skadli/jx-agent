"""stdlib http.server + asyncio 桥；server 在工作线程跑，请求体内 await 主 loop。"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sanshiliu.channels.web.auth import DashboardAuth, write_auth_error
from sanshiliu.channels.web.routes import Router
from sanshiliu.foundation.logging import get_logger

_logger = get_logger(__name__)


def _build_request_handler(
    router: Router,
    auth: DashboardAuth | None,
) -> type[BaseHTTPRequestHandler]:
    """工厂：每次实例化都是一个干净的 BaseHTTPRequestHandler 子类，闭包持有 router。"""

    class _Handler(BaseHTTPRequestHandler):
        # 静默默认日志（每请求一行），改走我们的 structlog
        def log_message(self, format: str, *args: object) -> None:
            _logger.debug("http", line=format % args)

        def _dispatch(self) -> None:
            clean = self.path.split("?", 1)[0]
            protected = clean == "/chat" or clean.startswith("/api/")
            auth_endpoint = clean.startswith("/api/auth/")
            if protected and not auth_endpoint and auth is not None and not auth.authorized(self.headers):
                write_auth_error(self)
                return
            handler = router.resolve(self.command, self.path)
            if handler is None:
                self.send_error(404, "Not Found")
                return
            try:
                handler(self)
            except Exception as exc:
                _logger.exception("http handler 异常", path=self.path, error=str(exc))
                # response 可能已发，二次 send_error 会异常；suppress 一层
                with contextlib.suppress(Exception):
                    self.send_error(500, "internal error")

        do_GET = _dispatch
        do_POST = _dispatch
        do_PUT = _dispatch
        do_DELETE = _dispatch

    return _Handler


class WebServer:
    """HTTP 服务管理；start() 起后台线程，stop() 优雅退出。"""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        router: Router,
        loop: asyncio.AbstractEventLoop,
        auth: DashboardAuth | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._router = router
        self._loop = loop
        self._auth = auth
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """启动后台线程跑 serve_forever。"""
        if self.is_running:
            return
        handler_cls = _build_request_handler(self._router, self._auth)
        self._server = ThreadingHTTPServer((self._host, self._port), handler_cls)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="sanshiliu-web",
            daemon=True,
        )
        self._thread.start()
        _logger.info("web server 启动", host=self._host, port=self._port)

    def stop(self, *, timeout: float = 5.0) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        _logger.info("web server 已停止")
