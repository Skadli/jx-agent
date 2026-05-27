"""Server-Sent Events 帧格式化与写入辅助。"""

from __future__ import annotations

from io import BufferedIOBase


def format_event(data: str, *, event: str | None = None, event_id: str | None = None) -> bytes:
    """构造一帧 SSE；data 内含 \\n 自动拆分为多行 data: 字段。"""
    parts: list[str] = []
    if event is not None:
        parts.append(f"event: {event}")
    if event_id is not None:
        parts.append(f"id: {event_id}")
    for line in data.split("\n"):
        parts.append(f"data: {line}")
    return ("\n".join(parts) + "\n\n").encode("utf-8")


def format_heartbeat() -> bytes:
    """SSE 心跳——以冒号起头的注释行，客户端忽略；用于穿过中间代理避免超时断连。"""
    return b": heartbeat\n\n"


def safe_write(stream: BufferedIOBase, payload: bytes) -> bool:
    """向 SSE 客户端写一帧；BrokenPipe/ConnectionReset 返回 False 让调用方退出。"""
    try:
        stream.write(payload)
        stream.flush()
        return True
    except (BrokenPipeError, ConnectionResetError, OSError):
        return False
