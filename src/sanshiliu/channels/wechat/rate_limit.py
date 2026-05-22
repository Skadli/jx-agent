"""微信限流；用 rate_limit_counters 表做滑动窗口计数，per-user 30/日 + 全局 2/分。"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sanshiliu.foundation.logging import get_logger
from sanshiliu.storage.db import Database

_logger = get_logger(__name__)

# 一日窗口对齐到 UTC 0 点；线上若需对齐北京时间可加偏移
_DAY_SEC = 86400
_MIN_SEC = 60


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    reason: str  # ok / user_quota / global_rps


class WechatRateLimiter:
    """每次 take() 一次性查 + 写两个 scope（user + global）。"""

    def __init__(
        self,
        db: Database,
        *,
        per_user_per_day: int = 30,
        global_per_minute: int = 2,
    ) -> None:
        self._db = db
        self._user_quota = per_user_per_day
        self._global_quota = global_per_minute

    async def take(self, wxid: str) -> RateLimitDecision:
        """检查 + 占额；返回 allowed/拒绝原因。"""
        now = int(time.time())
        day_window = (now // _DAY_SEC) * _DAY_SEC
        min_window = (now // _MIN_SEC) * _MIN_SEC

        # 用户日额
        user_count = await self._read_count(f"user:{wxid}", day_window)
        if user_count >= self._user_quota:
            _logger.info(
                "rate limit 拦截：用户日额",
                wxid=wxid, count=user_count, quota=self._user_quota,
            )
            return RateLimitDecision(allowed=False, reason="user_quota")

        # 全局分钟额
        global_count = await self._read_count("global", min_window)
        if global_count >= self._global_quota:
            _logger.info(
                "rate limit 拦截：全局分钟额",
                count=global_count, quota=self._global_quota,
            )
            return RateLimitDecision(allowed=False, reason="global_rps")

        # 通过，占两个额
        await self._bump(f"user:{wxid}", day_window)
        await self._bump("global", min_window)
        return RateLimitDecision(allowed=True, reason="ok")

    async def _read_count(self, scope: str, window_start: int) -> int:
        cur = await self._db._execute(  # noqa: SLF001
            "SELECT count FROM rate_limit_counters WHERE scope = ? AND window_start = ?",
            (scope, window_start),
        )
        row = cur.fetchone()
        return int(row["count"]) if row else 0

    async def _bump(self, scope: str, window_start: int) -> None:
        await self._db._execute(  # noqa: SLF001
            """
            INSERT INTO rate_limit_counters (scope, window_start, count)
            VALUES (?, ?, 1)
            ON CONFLICT(scope, window_start) DO UPDATE SET count = count + 1
            """,
            (scope, window_start),
        )
