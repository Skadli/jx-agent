"""异步指数退避重试装饰器；仅对白名单异常重试。"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import TypeVar

from sanshiliu.foundation.errors import LLMRetryableError
from sanshiliu.foundation.logging import get_logger

T = TypeVar("T")

_logger = get_logger(__name__)


def async_retry(
    *,
    max_attempts: int = 4,
    base: float = 0.5,
    cap: float = 8.0,
    jitter: float = 0.2,
    retry_on: type[Exception] | tuple[type[Exception], ...] = LLMRetryableError,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """async 指数退避装饰器；其他异常直接透传。"""
    if max_attempts < 1:
        raise ValueError("max_attempts 必须 >= 1")

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args: object, **kwargs: object) -> T:
            last_exc: BaseException | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except retry_on as exc:
                    last_exc = exc
                    if attempt >= max_attempts:
                        break
                    delay = min(base * (2 ** (attempt - 1)), cap) + random.uniform(0, jitter)
                    _logger.warning(
                        "调用失败，准备重试",
                        func=func.__qualname__,
                        attempt=attempt,
                        max_attempts=max_attempts,
                        delay_sec=round(delay, 3),
                        error=str(exc),
                    )
                    await asyncio.sleep(delay)
            # 所有尝试均失败
            assert last_exc is not None  # mypy
            _logger.error(
                "重试耗尽",
                func=func.__qualname__,
                attempts=max_attempts,
                error=str(last_exc),
            )
            raise last_exc

        return wrapper

    return decorator
