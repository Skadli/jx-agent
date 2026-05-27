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
        # Phase 10 migration：老库给 channel_messages 加 media 列；新库 CREATE TABLE 已含
        # ALTER TABLE ADD COLUMN 重复时抛 OperationalError("duplicate column name")，吞掉
        for alter_sql in (
            "ALTER TABLE channel_messages ADD COLUMN media TEXT",
            # PR1（2026-05-27）：sessions 表加 compact_summary / active_module_ids
            "ALTER TABLE sessions ADD COLUMN compact_summary TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE sessions ADD COLUMN active_module_ids TEXT NOT NULL DEFAULT ''",
            # PR3（2026-05-27）：permission_decisions 表加 source 列，区分自动决策与用户确认
            "ALTER TABLE permission_decisions ADD COLUMN source TEXT NOT NULL DEFAULT 'unknown'",
        ):
            try:
                conn.execute(alter_sql)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
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

    async def save_session_state(
        self,
        *,
        session_id: str,
        compact_summary: str,
        active_module_ids: str,
    ) -> None:
        """PR1：刷新会话非消息状态（compact_summary + 活跃模块 id 列表）。

        messages 不在 sqlite，落到 <data_dir>/sessions/<id>.jsonl。
        active_module_ids 用逗号分隔的字符串表示；reload 时按 split(",") 还原 set。
        """
        await self._execute(
            """
            UPDATE sessions
            SET compact_summary = ?, active_module_ids = ?, last_active_at = ?
            WHERE id = ?
            """,
            (
                compact_summary,
                active_module_ids,
                int(time.time() * 1000),
                session_id,
            ),
        )

    async def find_recent_session_id(
        self,
        *,
        channel: str,
        user_id: str | None,
    ) -> str | None:
        """PR1：按 channel + user_id 找最近活跃的 session_id；用于通道启动 reload。

        user_id 为 None 时严格匹配 user_id IS NULL（REPL 单用户场景）。
        """
        if user_id is None:
            cur = await self._execute(
                """
                SELECT id FROM sessions
                WHERE channel = ? AND user_id IS NULL
                ORDER BY last_active_at DESC LIMIT 1
                """,
                (channel,),
            )
        else:
            cur = await self._execute(
                """
                SELECT id FROM sessions
                WHERE channel = ? AND user_id = ?
                ORDER BY last_active_at DESC LIMIT 1
                """,
                (channel, user_id),
            )
        row = await asyncio.to_thread(cur.fetchone)
        return str(row["id"]) if row else None

    async def list_recent_sessions_for_prompt(
        self,
        *,
        channel: str,
        user_id: str | None,
        limit: int = 5,
        exclude_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """LoadMemory 扩展（2026-05-27）：返同 channel + user_id 最近活跃 sessions。

        - user_id IS NULL 严格匹配（REPL 场景）vs 给定 user_id 等值匹配——
          复用 find_recent_session_id 同样的语义；
        - exclude_id 非 None 时排除当前 session（注入 Recent Sessions 段时用）；
        - 返 list[{id, channel, user_id, created_at, last_active_at, compact_summary}]。
        """
        # 拼 SQL：user_id 分支 + 可选 exclude_id 分支
        params: list[Any] = [channel]
        user_clause = "user_id IS NULL" if user_id is None else "user_id = ?"
        if user_id is not None:
            params.append(user_id)
        exclude_clause = ""
        if exclude_id is not None:
            exclude_clause = " AND id != ?"
            params.append(exclude_id)
        params.append(int(limit))
        sql = (
            "SELECT id, channel, user_id, created_at, last_active_at, compact_summary "
            "FROM sessions "
            f"WHERE channel = ? AND {user_clause}{exclude_clause} "
            "ORDER BY last_active_at DESC LIMIT ?"
        )
        cur = await self._execute(sql, tuple(params))
        rows = await asyncio.to_thread(cur.fetchall)
        return [dict(r) for r in rows]

    # Phase 8 权限决策
    async def insert_permission_decision(
        self,
        *,
        session_id: str,
        tool_name: str,
        decision: str,
        scope: str,
        pattern: str | None,
        source: str,
    ) -> int:
        """记一条权限决定；session 范围内 dispatcher 可复用此查询。

        PR3：source 区分自动决策（settings-allow / settings-deny / path-guard /
        default-mode / auto-allowable / session-cache / ask-no-confirmer / ask-error）
        与用户主动确认（user-confirmed）。
        """
        cur = await self._execute(
            """
            INSERT INTO permission_decisions
              (ts, session_id, tool_name, decision, scope, pattern, source)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                int(time.time() * 1000),
                session_id,
                tool_name,
                decision,
                scope,
                pattern,
                source,
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

    async def delete_session(self, session_id: str) -> dict[str, int]:
        """删除一个会话及其 dashboard 可见历史记录；返回每张表删除行数。"""
        if self._conn is None:
            raise StorageError("数据库未连接；先调用 await db.connect()")
        async with self._lock:
            return await asyncio.to_thread(self._delete_session_sync, session_id)

    def _delete_session_sync(self, session_id: str) -> dict[str, int]:
        if self._conn is None:
            raise StorageError("数据库未连接；先调用 await db.connect()")

        counts: dict[str, int] = {}
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            for table in (
                "channel_messages",
                "permission_decisions",
                "tool_calls",
                "skill_activations",
                "llm_calls",
            ):
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE session_id = ?",
                    (session_id,),
                )
                counts[table] = int(cur.rowcount if cur.rowcount >= 0 else 0)

            cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            counts["sessions"] = int(cur.rowcount if cur.rowcount >= 0 else 0)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return counts

    # ──────────── dashboard 聚合查询 ────────────

    async def list_recent_sessions(
        self,
        *,
        limit: int = 50,
        channel: str | None = None,
    ) -> list[dict[str, Any]]:
        """聚合 sessions + llm_calls；返回最近活跃排序。"""
        where = ""
        params: tuple[Any, ...] = ()
        if channel:
            where = "WHERE s.channel = ?"
            params = (channel,)
        sql = f"""
            SELECT
              s.id, s.channel, s.user_id, s.created_at, s.last_active_at,
              COALESCE(SUM(c.input_tokens), 0)  AS input_tokens,
              COALESCE(SUM(c.output_tokens), 0) AS output_tokens,
              COALESCE(SUM(c.cost_cny), 0)       AS cost_cny,
              COUNT(c.id) AS calls
            FROM sessions s
            LEFT JOIN llm_calls c
              ON c.session_id = s.id AND c.channel != 'compact-internal'
            {where}
            GROUP BY s.id
            ORDER BY s.last_active_at DESC
            LIMIT ?
        """
        cur = await self._execute(sql, (*params, int(limit)))
        rows = await asyncio.to_thread(cur.fetchall)
        return [dict(r) for r in rows]

    async def list_recent_tool_calls(
        self,
        *,
        limit: int = 50,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if session_id:
            cur = await self._execute(
                "SELECT * FROM tool_calls WHERE session_id = ? ORDER BY ts DESC LIMIT ?",
                (session_id, int(limit)),
            )
        else:
            cur = await self._execute(
                "SELECT * FROM tool_calls ORDER BY ts DESC LIMIT ?",
                (int(limit),),
            )
        rows = await asyncio.to_thread(cur.fetchall)
        return [dict(r) for r in rows]

    async def list_recent_permissions(
        self, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        cur = await self._execute(
            "SELECT * FROM permission_decisions ORDER BY ts DESC LIMIT ?",
            (int(limit),),
        )
        rows = await asyncio.to_thread(cur.fetchall)
        return [dict(r) for r in rows]

    async def list_skill_activations(
        self,
        *,
        limit: int = 50,
        skill_id: str | None = None,
        since_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if skill_id:
            where.append("skill_id = ?")
            params.append(skill_id)
        if since_ms is not None:
            where.append("ts >= ?")
            params.append(since_ms)
        wsql = ("WHERE " + " AND ".join(where)) if where else ""
        params.append(int(limit))
        cur = await self._execute(
            f"SELECT * FROM skill_activations {wsql} ORDER BY ts DESC LIMIT ?",
            tuple(params),
        )
        rows = await asyncio.to_thread(cur.fetchall)
        return [dict(r) for r in rows]

    async def count_skill_hits(self, *, since_ms: int) -> dict[str, int]:
        """按 skill_id 聚合命中次数；用于 dashboard 24h/7d 数字。"""
        cur = await self._execute(
            "SELECT skill_id, COUNT(*) AS n FROM skill_activations WHERE ts >= ? GROUP BY skill_id",
            (int(since_ms),),
        )
        rows = await asyncio.to_thread(cur.fetchall)
        return {r["skill_id"]: int(r["n"]) for r in rows}

    async def aggregate_overview(self, *, since_ms: int) -> dict[str, Any]:
        """单次聚合：总 tokens / cost / 平均延迟 / 通道分布。"""
        cur = await self._execute(
            """
            SELECT
              COUNT(*) AS calls,
              COALESCE(SUM(input_tokens), 0)  AS input_tokens,
              COALESCE(SUM(output_tokens), 0) AS output_tokens,
              COALESCE(SUM(cost_cny), 0)       AS cost_cny,
              COALESCE(AVG(latency_ms), 0)     AS avg_latency_ms
            FROM llm_calls
            WHERE ts >= ? AND channel != 'compact-internal'
            """,
            (int(since_ms),),
        )
        row = await asyncio.to_thread(cur.fetchone)
        agg = dict(row) if row else {}

        cur2 = await self._execute(
            """
            SELECT channel, COUNT(DISTINCT session_id) AS n
            FROM llm_calls
            WHERE ts >= ? AND channel != 'compact-internal'
            GROUP BY channel
            """,
            (int(since_ms),),
        )
        ch_rows = await asyncio.to_thread(cur2.fetchall)
        agg["channels"] = {r["channel"]: int(r["n"]) for r in ch_rows}

        cur3 = await self._execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE last_active_at >= ?",
            (int(since_ms),),
        )
        row3 = await asyncio.to_thread(cur3.fetchone)
        agg["active_sessions"] = int(row3["n"]) if row3 else 0

        cur4 = await self._execute("SELECT COUNT(*) AS n FROM sessions")
        row4 = await asyncio.to_thread(cur4.fetchone)
        agg["total_sessions"] = int(row4["n"]) if row4 else 0

        # Phase 10：按 base_url 分组聚合，让 dashboard 区分 doubao / DeepSeek 等后端
        cur5 = await self._execute(
            """
            SELECT
              base_url,
              COUNT(*)                          AS calls,
              COALESCE(SUM(input_tokens), 0)    AS input_tokens,
              COALESCE(SUM(output_tokens), 0)   AS output_tokens,
              COALESCE(SUM(cost_cny), 0)        AS cost_cny
            FROM llm_calls
            WHERE ts >= ? AND channel != 'compact-internal'
            GROUP BY base_url
            """,
            (int(since_ms),),
        )
        by_rows = await asyncio.to_thread(cur5.fetchall)
        agg["by_provider"] = {
            str(r["base_url"]): {
                "calls":         int(r["calls"]),
                "input_tokens":  int(r["input_tokens"]),
                "output_tokens": int(r["output_tokens"]),
                "cost_cny":      float(r["cost_cny"]),
            }
            for r in by_rows
        }
        return agg

    async def insert_tool_call(
        self,
        *,
        session_id: str,
        tool_name: str,
        arguments: str,
        result_text: str | None,
        is_error: bool,
        latency_ms: int,
        permission_decision: str | None,
    ) -> int:
        cur = await self._execute(
            """
            INSERT INTO tool_calls
              (ts, session_id, tool_name, arguments, result_text,
               is_error, latency_ms, permission_decision)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                int(time.time() * 1000),
                session_id, tool_name, arguments,
                (result_text or "")[:2048],
                1 if is_error else 0,
                int(latency_ms),
                permission_decision,
            ),
        )
        return int(cur.lastrowid or 0)

    async def insert_skill_activation(
        self,
        *,
        session_id: str,
        skill_id: str,
        trigger: str | None,
        user_text: str | None,
    ) -> int:
        cur = await self._execute(
            """
            INSERT INTO skill_activations (ts, session_id, skill_id, trigger, user_text)
            VALUES (?,?,?,?,?)
            """,
            (
                int(time.time() * 1000),
                session_id, skill_id, trigger,
                (user_text or "")[:512],
            ),
        )
        return int(cur.lastrowid or 0)


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
