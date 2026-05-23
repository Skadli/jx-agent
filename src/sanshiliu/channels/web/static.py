"""dashboard 静态文件托管；GET / 重定向到 /dashboard/，GET /dashboard/* 读 dashboard 目录。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from sanshiliu.foundation.logging import get_logger

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler

_logger = get_logger(__name__)

# 仅允许这些后缀对外暴露；防止误传 .py / .db / .env 等
_ALLOWED_SUFFIXES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".js":   "application/javascript; charset=utf-8",
    ".jsx":  "text/babel; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
    ".svg":  "image/svg+xml",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".webp": "image/webp",
    ".ico":  "image/x-icon",
    ".json": "application/json; charset=utf-8",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".map":  "application/json; charset=utf-8",
}


def make_root_redirect_handler() -> Callable[[BaseHTTPRequestHandler], None]:
    """GET / → 302 → /dashboard/"""

    def handler(req: BaseHTTPRequestHandler) -> None:
        req.send_response(302)
        req.send_header("Location", "/dashboard/")
        req.send_header("Content-Length", "0")
        req.end_headers()

    return handler


def make_dashboard_handler(
    dashboard_dir: Path,
) -> Callable[[BaseHTTPRequestHandler], None]:
    """GET /dashboard/* → 从 dashboard_dir 读文件返回；防 .. 穿越。"""

    base = dashboard_dir.resolve()

    def handler(req: BaseHTTPRequestHandler) -> None:
        # 去掉 query 串、规范化路径
        raw = req.path.split("?", 1)[0]
        # 去掉前缀；空 → 默认 index
        rel = raw[len("/dashboard"):].lstrip("/")
        if rel == "" or rel.endswith("/"):
            rel = (rel + "dashboard.html").lstrip("/")

        target = (base / rel).resolve()
        # 防穿越：必须仍在 base 下
        try:
            target.relative_to(base)
        except ValueError:
            req.send_error(403, "forbidden")
            return

        if not target.is_file():
            req.send_error(404, "not found")
            return

        suffix = target.suffix.lower()
        mime = _ALLOWED_SUFFIXES.get(suffix)
        if mime is None:
            req.send_error(403, "file type not allowed")
            return

        try:
            data = target.read_bytes()
        except OSError as exc:
            _logger.warning("dashboard 静态文件读失败", path=str(target), error=str(exc))
            req.send_error(500, "read error")
            return

        req.send_response(200)
        req.send_header("Content-Type", mime)
        req.send_header("Content-Length", str(len(data)))
        req.send_header("Cache-Control", "no-cache")
        req.end_headers()
        req.wfile.write(data)

    return handler
