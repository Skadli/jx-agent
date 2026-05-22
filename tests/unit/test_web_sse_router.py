"""SSE + Router 单测。"""

from __future__ import annotations

import io

from sanshiliu.channels.web.routes import Router
from sanshiliu.channels.web.sse import format_event, format_heartbeat, safe_write


def test_format_event_basic() -> None:
    out = format_event("hello")
    assert out == b"data: hello\n\n"


def test_format_event_multiline_data() -> None:
    out = format_event("line1\nline2")
    assert out == b"data: line1\ndata: line2\n\n"


def test_format_event_with_event_and_id() -> None:
    out = format_event("payload", event="done", event_id="1")
    text = out.decode("utf-8")
    assert text.startswith("event: done\n")
    assert "id: 1\n" in text
    assert text.endswith("data: payload\n\n")


def test_format_heartbeat_is_comment() -> None:
    assert format_heartbeat() == b": heartbeat\n\n"


def test_safe_write_ok() -> None:
    buf = io.BytesIO()
    assert safe_write(buf, b"x") is True
    assert buf.getvalue() == b"x"


def test_safe_write_handles_broken_pipe() -> None:
    class _Broken:
        def write(self, _data: bytes) -> int:
            raise BrokenPipeError

        def flush(self) -> None:
            pass

    assert safe_write(_Broken(), b"x") is False  # type: ignore[arg-type]


def test_router_exact_match() -> None:
    r = Router()

    def h(_req: object) -> None:
        pass

    r.register("GET", "/healthz", h)
    assert r.resolve("GET", "/healthz") is h
    assert r.resolve("POST", "/healthz") is None
    assert r.resolve("GET", "/other") is None


def test_router_prefix_match() -> None:
    r = Router()

    def h(_req: object) -> None:
        pass

    r.register_prefix("POST", "/wechat/", h)
    assert r.resolve("POST", "/wechat/webhook") is h
    assert r.resolve("GET", "/wechat/webhook") is None


def test_router_exact_takes_priority_over_prefix() -> None:
    r = Router()

    def h_exact(_: object) -> None:
        pass

    def h_prefix(_: object) -> None:
        pass

    r.register("GET", "/a/b", h_exact)
    r.register_prefix("GET", "/a/", h_prefix)
    assert r.resolve("GET", "/a/b") is h_exact
    assert r.resolve("GET", "/a/c") is h_prefix
