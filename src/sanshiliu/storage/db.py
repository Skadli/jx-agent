"""sqlite DAO；async 接口通过 asyncio.to_thread 包装 stdlib sqlite3。"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Any

from sanshiliu.foundation.errors import StorageError
from sanshiliu.foundation.logging import get_logger

_logger = get_logger(__name__)

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Database:
    """sqlite DAO 封装；生产走单例，测试可直接构造。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()  # 串行化写，防 SQLITE_BUSY

    async def connect(self) -> None:
        """打开连接 + 执行 schema（幂等）。"""
        if self._conn is not None:
            return
        await asyncio.to_thread(self._connect_sync)
        _logger.info("数据库就绪", path=str(self._db_path))

    def _connect_sync(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # 允许 asyncio.to_thread 跨线程使用
        conn = sqlite3.connect(
            self._db_path,
            isolation_level=None,  # 自动 commit 模式，由我们显式 BEGIN
            check_same_thread=False,
            timeout=30.0,
        )
        conn.row_factory = sqlite3.Row
        # 执行 schema
        schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        conn.executescript(schema_sql)
        self._conn = conn

    async def close(self) -> None:
        if self._conn is None:
            return
        conn = self._conn
        self._conn = None
        await asyncio.to_thread(conn.close)

    # 内部执行
    async def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        if self._conn is None:
            raise StorageError("数据库未连接；先调用 await db.connect()")
        async with self._lock:
            return await asyncio.to_thread(self._conn.execute, sql, params)

    async def _executemany(self, sql: str, seq: list[tuple[Any, ...]]) -> None:
        if self._conn is None:
            raise StorageError("数据库未连接；先调用 await db.connect()")
        async with self._lock:
            await asyncio.to_thread(self._conn.executemany, sql, seq)

    # llm_calls
    async def insert_llm_call(
        self,
        *,
        session_id: str,
        channel: str,
        user_id: str | None,
        model: str,
        base_url: str,
        input_tokens: int,
        output_tokens: int,
        cost_cny: float,
        latency_ms: int,
        stop_reason: str | None,
        error: str | None = None,
    ) -> int:
        """落一行 LLM 调用记账；返回自增 id。"""
        cur = await self._execute(
            """
            INSERT INTO llm_calls
              (ts, session_id, channel, user_id, model, base_url,
               input_tokens, output_tokens, cost_cny, latency_ms,
               stop_reason, error)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(time.time() * 1000),
                session_id,
                channel,
                user_id,
                model,
                base_url,
                input_tokens,
                output_tokens,
                cost_cny,
                latency_ms,
                stop_reason,
                error,
            ),
        )
        return int(cur.lastrowid or 0)

    # sessions
    async def upsert_session(
        self,
        *,
        session_id: str,
        channel: str,
        user_id: str | None,
    ) -> None:
        """插入或刷新会话最近活跃时间。"""
        now_ms = int(time.time() * 1000)
        await self._execute(
            """
            INSERT INTO sessions (id, channel, user_id, created_at, last_active_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET last_active_at = excluded.last_active_at
            """,
            (session_id, channel, user_id, now_ms, now_ms),
        )

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        cur = await self._execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        row = await asyncio.to_thread(cur.fetchone)
        return dict(row) if row else None

    # Phase 8 权限决策
    async def insert_permission_decision(
        self,
        *,
        session_id: str,
        tool_name: str,
        decision: str,
        scope: str,
        pattern: str | None,
    ) -> int:
        """记一条权限决定；session 范围内 dispatcher 可复用此查询。"""
        cur = await self._execute(
            """
            INSERT INTO permission_decisions
              (ts, session_id, tool_name, decision, scope, pattern)
            VALUES (?,?,?,?,?,?)
            """,
            (
                int(time.time() * 1000),
                session_id,
                tool_name,
                decision,
                scope,
                pattern,
            ),
        )
        return int(cur.lastrowid or 0)

    async def list_session_permissions(
        self, session_id: str
    ) -> list[dict[str, Any]]:
        """读取本会话内所有"session"范围决定；进程重启可重建缓存。"""
        cur = await self._execute(
            """
            SELECT tool_name, decision, scope, pattern, ts
            FROM permission_decisions
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (session_id,),
        )
        rows = await asyncio.to_thread(cur.fetchall)
        return [dict(r) for r in rows]

    # REPL /stats 统计
    async def get_session_stats(self, session_id: str) -> dict[str, int | float]:
        """返回该会话的 token 用量、调用次数等汇总。"""
        cur = await self._execute(
            """
            SELECT
              COUNT(*) AS calls,
              COALESCE(SUM(input_tokens), 0)  AS input_tokens,
              COALESCE(SUM(output_tokens), 0) AS output_tokens,
              COALESCE(SUM(cost_cny), 0)       AS cost_cny
            FROM llm_calls
            WHERE session_id = ?
            """,
            (session_id,),
        )
        row = await asyncio.to_thread(cur.fetchone)
        return dict(row) if row else {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_cny": 0.0}


_db_singleton: Database | None = None


async def get_database(db_path: Path) -> Database:
    """获取数据库单例；首次调用会建表。"""
    global _db_singleton
    if _db_singleton is None:
        _db_singleton = Database(db_path)
        await _db_singleton.connect()
    return _db_singleton


async def reset_database_singleton() -> None:
    """测试用：复位单例。"""
    global _db_singleton
    if _db_singleton is not None:
        await _db_singleton.close()
        _db_singleton = None
