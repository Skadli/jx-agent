"""路由注册器；handler 按 (method, path) 分发到对应 callable。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# Handler 签名：handler(request_handler) -> None
# request_handler 是 BaseHTTPRequestHandler 实例，含 wfile/rfile/headers
Handler = Callable[[Any], None]


@dataclass
class Router:
    """极简路由表；按 (METHOD, exact_path) 查找；前缀路由用 register_prefix。"""

    _exact: dict[tuple[str, str], Handler] = field(default_factory=dict)
    _prefix: list[tuple[str, str, Handler]] = field(default_factory=list)

    def register(self, method: str, path: str, handler: Handler) -> None:
        self._exact[(method.upper(), path)] = handler

    def register_prefix(self, method: str, prefix: str, handler: Handler) -> None:
        self._prefix.append((method.upper(), prefix, handler))

    def resolve(self, method: str, path: str) -> Handler | None:
        m = method.upper()
        # 剥离 query string；exact 路由按裸 path 匹配
        clean = path.split("?", 1)[0]
        if (m, clean) in self._exact:
            return self._exact[(m, clean)]
        for h_method, prefix, handler in self._prefix:
            if h_method == m and clean.startswith(prefix):
                return handler
        return None
