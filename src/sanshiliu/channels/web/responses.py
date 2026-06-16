"""dashboard 响应写出口的共享实现：JSON 序列化 + 按 Accept-Encoding 协商 gzip。

历史上每个 api_*.py 各抄了一份 _write_json，给历史会话 messages 端点加 gzip 时漏掉了其余 8 份；
统一到这里，避免再漂。SSE 流不走这里（必须不压、即时 flush）；静态文件用 maybe_gzip 自己拼头。
"""

from __future__ import annotations

import gzip
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler

# body 超过该阈值且客户端声明 Accept-Encoding: gzip 才压。
# 小响应压了反因 gzip 头/CRC 变大、且白耗 CPU（同 nginx gzip_min_length）。
_GZIP_MIN_BYTES = 1024
_GZIP_LEVEL = 6


def maybe_gzip(
    req: BaseHTTPRequestHandler, body: bytes, *, compressible: bool
) -> tuple[bytes, bool]:
    """按请求 Accept-Encoding 与 body 大小决定是否 gzip；返回 (body, 是否已压)。

    compressible=False（图片/字体等已压缩二进制）直接原样返回——再压无益反耗 CPU。
    调用方据返回的布尔决定是否补 Content-Encoding/Vary 头。
    """
    if (
        compressible
        and len(body) >= _GZIP_MIN_BYTES
        and "gzip" in req.headers.get("Accept-Encoding", "").lower()
    ):
        return gzip.compress(body, _GZIP_LEVEL), True
    return body, False


def write_json(req: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = 200) -> None:
    """序列化 payload 为 JSON 写出；够大且客户端支持时 gzip。dashboard 所有 JSON 端点共用。"""
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    body, gzipped = maybe_gzip(req, body, compressible=True)
    req.send_response(status)
    req.send_header("Content-Type", "application/json; charset=utf-8")
    if gzipped:
        req.send_header("Content-Encoding", "gzip")
        req.send_header("Vary", "Accept-Encoding")
    req.send_header("Content-Length", str(len(body)))
    req.send_header("Cache-Control", "no-store")
    req.end_headers()
    req.wfile.write(body)
