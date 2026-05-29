"""dashboard 读端点；GET /api/* 聚合 DB + loaders → JSON。

handler 工厂模式：runner 传入 db / 各 loader，闭包持有。所有处理失败都 try/except
回 500 并写日志，不抛到 dispatcher。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import urllib.parse
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sanshiliu.channels.web.handlers import HealthState
from sanshiliu.foundation.logging import get_logger
from sanshiliu.identity.loader import PersonaLoader
from sanshiliu.identity.types import MODULES_DIRNAME
from sanshiliu.memory.longterm.claudemd import ClaudeMdLoader
from sanshiliu.memory.longterm.memdir import MemdirLoader
from sanshiliu.security.settings_loader import SettingsLoader
from sanshiliu.skills.loader import SkillLoader
from sanshiliu.skills.structure import read_skill_structure, skill_structure_path
from sanshiliu.storage.db import Database
from sanshiliu.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler

_logger = get_logger(__name__)

# 24 小时窗口，给 overview KPI 默认用
_DEFAULT_RANGE_SEC = 24 * 3600
_RANGE_MAP = {
    "1h":  3600,
    "24h": 24 * 3600,
    "7d":  7 * 24 * 3600,
    "30d": 30 * 24 * 3600,
}


# ────────── 工具 ──────────

def _write_json(req: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    req.send_response(status)
    req.send_header("Content-Type", "application/json; charset=utf-8")
    req.send_header("Content-Length", str(len(body)))
    req.send_header("Cache-Control", "no-store")
    req.end_headers()
    req.wfile.write(body)


def _parse_query(path: str) -> dict[str, str]:
    if "?" not in path:
        return {}
    qs = path.split("?", 1)[1]
    out: dict[str, str] = {}
    for k, v in urllib.parse.parse_qsl(qs, keep_blank_values=True):
        out[k] = v
    return out


def _range_seconds(q: dict[str, str]) -> int:
    raw = q.get("range") or "24h"
    return _RANGE_MAP.get(raw, _DEFAULT_RANGE_SEC)


def _run(loop: asyncio.AbstractEventLoop, coro: Coroutine[Any, Any, Any]) -> Any:
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result(timeout=10.0)


def _safe(req: BaseHTTPRequestHandler, fn: Callable[[], None], where: str) -> None:
    try:
        fn()
    except Exception as exc:
        _logger.exception("api 处理失败", path=where, error=str(exc))
        with contextlib.suppress(Exception):
            _write_json(req, {"error": str(exc), "where": where}, status=500)


# ────────── /api/overview ──────────

def make_overview_handler(
    db: Database,
    loop: asyncio.AbstractEventLoop,
    persona_loader: PersonaLoader | None,
    memdir_loader: MemdirLoader | None,
    claudemd_loader: ClaudeMdLoader | None,
    skill_loader: SkillLoader | None,
    start_time: float,
    settings: Any,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            q = _parse_query(req.path)
            rng = _range_seconds(q)
            since_ms = int((time.time() - rng) * 1000)
            agg = _run(loop, db.aggregate_overview(since_ms=since_ms))

            persona_chars = 0
            persona_files = 0
            if persona_loader is not None:
                try:
                    snap = persona_loader.get()
                    persona_chars = snap.total_chars()
                    persona_files = len(snap.sections)
                except Exception:
                    pass

            memdir_count = 0
            claude_chars = 0
            if memdir_loader is not None:
                with contextlib.suppress(Exception):
                    memdir_count = len(memdir_loader.get().entries)
            if claudemd_loader is not None:
                with contextlib.suppress(Exception):
                    claude_chars = claudemd_loader.get().total_chars()

            skills_count = 0
            if skill_loader is not None:
                with contextlib.suppress(Exception):
                    skills_count = len(skill_loader.list())

            payload = {
                "version": "1.0.0",
                "model":   getattr(settings, "openai_model", ""),
                "base_url": getattr(settings, "openai_base_url", ""),
                "uptime_sec": int(time.time() - start_time),
                "range_sec": rng,
                "stats": {
                    "calls":          int(agg.get("calls", 0) or 0),
                    "input_tokens":   int(agg.get("input_tokens", 0) or 0),
                    "output_tokens":  int(agg.get("output_tokens", 0) or 0),
                    "cost_cny":       float(agg.get("cost_cny", 0) or 0),
                    "avg_latency_ms": float(agg.get("avg_latency_ms", 0) or 0),
                    "active_sessions": int(agg.get("active_sessions", 0) or 0),
                    "total_sessions":  int(agg.get("total_sessions", 0) or 0),
                    "channels":        agg.get("channels", {}),
                    # Phase 10：base_url → {calls, tokens, cost} 后端分账
                    "by_provider":     agg.get("by_provider", {}),
                },
                "identity": {
                    "persona_chars": persona_chars,
                    "persona_files": persona_files,
                    "memdir_count":  memdir_count,
                    "claudemd_chars": claude_chars,
                    "skills_count":  skills_count,
                },
            }
            _write_json(req, payload)

        _safe(req, _do, "/api/overview")

    return handler


# ────────── /api/health ──────────

def make_health_api_handler(
    health: HealthState,
    loop: asyncio.AbstractEventLoop,
    db: Database,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            # 顺便 ping 一下 db，让 healthz 也准
            async def _ping() -> bool:
                try:
                    cur = await db._execute("SELECT 1 AS ok")
                    row = cur.fetchone()
                    return bool(row and row["ok"] == 1)
                except Exception:
                    return False
            ok = _run(loop, _ping())
            health.set("db", "up" if ok else "down")
            _write_json(req, health.snapshot())
        _safe(req, _do, "/api/health")
    return handler


# ────────── /api/sessions ──────────

def make_sessions_handler(
    db: Database,
    loop: asyncio.AbstractEventLoop,
    data_dir: Path,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            q = _parse_query(req.path)
            limit = int(q.get("limit") or 50)
            channel = q.get("channel") or None
            rows = _run(loop, db.list_recent_sessions(limit=limit, channel=channel))

            # 给每个会话补"最后一句话"（从 jsonl 末尾找最后 user 文本）
            sessions_dir = data_dir / "sessions"
            for r in rows:
                r["last_message"] = _read_last_message(sessions_dir, r["id"])
            _write_json(req, {"sessions": rows})
        _safe(req, _do, "/api/sessions")
    return handler


def _read_last_message(sessions_dir: Path, session_id: str) -> str:
    """从 jsonl 文件末尾找最后一条 user 消息文本；找不到返回空。"""
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")[:120] or "default"
    path = sessions_dir / f"{safe}.jsonl"
    if not path.is_file():
        return ""
    try:
        # 文件可能很大；只读最后 32KB
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > 32768:
                f.seek(-32768, 2)
                f.readline()  # 丢半行
            tail = f.read().decode("utf-8", errors="ignore")
        lines = [ln for ln in tail.splitlines() if ln.strip()]
        for ln in reversed(lines):
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            msgs = rec.get("messages") or []
            for m in reversed(msgs):
                if m.get("role") == "user" and m.get("content"):
                    txt = str(m["content"]).strip()
                    return txt[:120]
            return ""
    except OSError:
        return ""
    return ""


# ────────── /api/sessions/{id}/messages ──────────

def make_session_messages_handler(
    data_dir: Path,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            raw = req.path.split("?", 1)[0]
            # /api/sessions/{id}/messages
            parts = raw.strip("/").split("/")
            if len(parts) < 4 or parts[3] != "messages":
                _write_json(req, {"error": "bad path"}, status=400)
                return
            # wechat session_id 形如 "wechat:user:o9...@im.wechat"，前端 encodeURIComponent
            # 后变成 %3A / %40 等；必须先 unquote，否则 sanitize 出的文件名与写入端不一致 → 永远空数组
            session_id = urllib.parse.unquote(parts[2])
            safe = "".join(c for c in session_id if c.isalnum() or c in "-_")[:120]
            path = data_dir / "sessions" / f"{safe}.jsonl"
            messages: list[dict[str, Any]] = []
            if path.is_file():
                with path.open("r", encoding="utf-8") as f:
                    last_record: dict[str, Any] | None = None
                    for ln in f:
                        ln = ln.strip()
                        if not ln:
                            continue
                        try:
                            last_record = json.loads(ln)
                        except json.JSONDecodeError:
                            continue
                    if last_record is not None:
                        for m in (last_record.get("messages") or []):
                            # 跳过 system；其它都保留，前端会按 role + tool_calls 渲染
                            role = m.get("role")
                            if role not in ("user", "assistant", "tool"):
                                continue
                            messages.append({
                                "role": role,
                                "content": m.get("content") or "",
                                "tool_calls": m.get("tool_calls"),
                                "tool_call_id": m.get("tool_call_id"),
                                "name": m.get("name"),
                            })
            _write_json(req, {"session_id": session_id, "messages": messages})
        _safe(req, _do, "/api/sessions/messages")
    return handler


# ────────── /api/tool_calls ──────────

def make_tool_calls_handler(
    db: Database,
    loop: asyncio.AbstractEventLoop,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            q = _parse_query(req.path)
            limit = int(q.get("limit") or 50)
            session = q.get("session") or None
            rows = _run(loop, db.list_recent_tool_calls(limit=limit, session_id=session))
            _write_json(req, {"tool_calls": rows})
        _safe(req, _do, "/api/tool_calls")
    return handler


# ────────── /api/tools ──────────

def make_tools_handler(
    tool_registry: ToolRegistry | None,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            tools: list[dict[str, Any]] = []
            if tool_registry is not None:
                for definition in tool_registry.definitions():
                    tools.append({
                        "name": definition.name,
                        "description": definition.description,
                        "input_schema": definition.input_schema,
                    })
            _write_json(req, {
                "enabled": bool(tool_registry is not None and not tool_registry.is_empty),
                "tools": tools,
            })
        _safe(req, _do, "/api/tools")
    return handler


# ────────── /api/persona ──────────

_PERSONA_SUMMARY = {
    "identity.md":      "我是谁 · 背景 · 红线",
    "style.md":         "说话风格硬约束 + anti-pattern",
    "personality.md":   "性格八维 + OCEAN",
    "beliefs.md":       "价值观底线 · 红线",
    "fewshot_short.md": "短样本 (微信节奏)",
}


def make_persona_handler(
    persona_loader: PersonaLoader | None,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            if persona_loader is None:
                _write_json(req, {"files": [], "total_chars": 0})
                return
            snap = persona_loader.get()
            files = []
            for name, body in snap.sections.items():
                mtime = snap.mtimes.get(name, 0)
                files.append({
                    "name":    name,
                    "chars":   len(body),
                    "mtime":   mtime,
                    "summary": _PERSONA_SUMMARY.get(name, ""),
                })
            _write_json(req, {
                "files":       files,
                "total_chars": snap.total_chars(),
                "dir":         str(snap.persona_dir),
            })
        _safe(req, _do, "/api/persona")
    return handler


def resolve_persona_file(persona_loader: PersonaLoader, fname: str) -> Path | None:
    """按 basename 查 core/ 优先、modules/ fallback；通过 .resolve() 校验仍在 persona_dir 之下，
    防 `..` path traversal。找不到返回 None。"""
    persona_root = persona_loader.persona_dir.resolve()
    for candidate in (
        persona_loader.core_dir / fname,
        persona_loader.persona_dir / MODULES_DIRNAME / fname,
    ):
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        try:
            resolved.relative_to(persona_root)
        except ValueError:
            continue
        if resolved.is_file():
            return resolved
    return None


def make_persona_file_handler(
    persona_loader: PersonaLoader | None,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            if persona_loader is None:
                _write_json(req, {"error": "persona disabled"}, status=404)
                return
            raw = req.path.split("?", 1)[0]
            # /api/persona/{filename}
            parts = raw.strip("/").split("/")
            if len(parts) < 3:
                _write_json(req, {"error": "bad path"}, status=400)
                return
            # 与 write handler 保持一致：unquote URL-encoded segment
            fname = urllib.parse.unquote(parts[2])
            # 安全：只允许 .md
            if not fname.endswith(".md") or "/" in fname or ".." in fname:
                _write_json(req, {"error": "invalid filename"}, status=400)
                return
            path = resolve_persona_file(persona_loader, fname)
            if path is None:
                _write_json(req, {"error": "not found"}, status=404)
                return
            body = path.read_text(encoding="utf-8")
            _write_json(req, {
                "name":  fname,
                "body":  body,
                "chars": len(body),
                "mtime": path.stat().st_mtime,
            })
        _safe(req, _do, "/api/persona/file")
    return handler


# ────────── /api/memory ──────────

def make_memory_handler(
    memdir_loader: MemdirLoader | None,
    claudemd_loader: ClaudeMdLoader | None,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            entries: list[dict[str, Any]] = []
            if memdir_loader is not None:
                try:
                    snap = memdir_loader.get()
                    for e in snap.entries:
                        entries.append({
                            "file":        e.file_path.name,
                            "scope":       e.memory_type,
                            "name":        e.name,
                            "description": e.description,
                            "chars":       len(e.body),
                            "mtime":       e.file_path.stat().st_mtime if e.file_path.is_file() else 0,
                            "protected":   e.protected,
                        })
                except Exception as exc:
                    _logger.warning("memdir 读失败", error=str(exc))

            claude_md: dict[str, Any] | None = None
            if claudemd_loader is not None:
                try:
                    cmd_snap = claudemd_loader.get()
                    claude_md = {
                        "global_chars":  len(cmd_snap.global_text),
                        "project_chars": len(cmd_snap.project_text),
                        "global_path":   str(cmd_snap.global_path),
                        "project_path":  str(cmd_snap.project_path),
                        "total_chars":   cmd_snap.total_chars(),
                    }
                except Exception:
                    pass

            _write_json(req, {
                "entries":  entries,
                "claudemd": claude_md,
            })
        _safe(req, _do, "/api/memory")
    return handler


def make_memory_file_handler(
    memdir_loader: MemdirLoader | None,
    claudemd_loader: ClaudeMdLoader | None,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            raw = req.path.split("?", 1)[0]
            # /api/memory/{path...}
            rest = raw[len("/api/memory/"):]
            if not rest:
                _write_json(req, {"error": "bad path"}, status=400)
                return
            # 特殊：__claudemd__
            if rest == "__claudemd__":
                if claudemd_loader is None:
                    _write_json(req, {"error": "disabled"}, status=404)
                    return
                snap = claudemd_loader.get()
                body = snap.assembled()
                _write_json(req, {
                    "path":  "CLAUDE.md",
                    "body":  body,
                    "chars": len(body),
                })
                return
            if memdir_loader is None:
                _write_json(req, {"error": "memdir disabled"}, status=404)
                return
            # 防穿越
            fname = urllib.parse.unquote(rest)
            if "/" in fname or ".." in fname or not fname.endswith(".md"):
                _write_json(req, {"error": "invalid filename"}, status=400)
                return
            path = memdir_loader.root / fname
            if not path.is_file():
                _write_json(req, {"error": "not found"}, status=404)
                return
            body = path.read_text(encoding="utf-8")
            _write_json(req, {
                "path":  fname,
                "body":  body,
                "chars": len(body),
                "mtime": path.stat().st_mtime,
            })
        _safe(req, _do, "/api/memory/file")
    return handler


# ────────── /api/skills ──────────

def make_skills_handler(
    skill_loader: SkillLoader | None,
    db: Database,
    loop: asyncio.AbstractEventLoop,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            if skill_loader is None:
                _write_json(req, {"skills": []})
                return
            now_ms = int(time.time() * 1000)
            hits_24h = _run(loop, db.count_skill_hits(since_ms=now_ms - 24 * 3600 * 1000))
            hits_7d  = _run(loop, db.count_skill_hits(since_ms=now_ms - 7 * 24 * 3600 * 1000))
            skills = []
            for s in skill_loader.list():
                skills.append({
                    "id":          s.id,
                    "name":        s.name,
                    "description": s.description,
                    "keywords":    s.keywords,
                    "chars":       len(s.body),
                    "source":      str(s.source),
                    "structure":   str(skill_structure_path(s)),
                    "priority":    s.priority,
                    "hits_24h":    int(hits_24h.get(s.id, 0)),
                    "hits_7d":     int(hits_7d.get(s.id, 0)),
                })
            _write_json(req, {"skills": skills})
        _safe(req, _do, "/api/skills")
    return handler


# ────────── /api/skills/{id}/structure ──────────

def make_skill_structure_handler(
    skill_loader: SkillLoader | None,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            if skill_loader is None:
                _write_json(req, {"error": "skills disabled"}, status=404)
                return
            raw = req.path.split("?", 1)[0]
            # /api/skills/{id}/structure —— register_prefix 会兜住 /api/skills/foo/reload 等，
            # 必须严格校验路径形状，否则把 reload 误吃了
            parts = raw.strip("/").split("/")
            if len(parts) != 4 or parts[0] != "api" or parts[1] != "skills" or parts[3] != "structure":
                _write_json(req, {"error": "not found"}, status=404)
                return
            skill_id = urllib.parse.unquote(parts[2])
            skill = next((s for s in skill_loader.list() if s.id == skill_id), None)
            if skill is None:
                _write_json(req, {"error": "skill not found", "id": skill_id}, status=404)
                return
            try:
                structure = read_skill_structure(skill)
            except FileNotFoundError:
                _write_json(req, {
                    "error": "skill structure not found",
                    "id": skill_id,
                    "path": str(skill_structure_path(skill)),
                }, status=404)
                return
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                _write_json(req, {
                    "error": "invalid skill structure",
                    "id": skill_id,
                    "path": str(skill_structure_path(skill)),
                    "detail": str(exc),
                }, status=500)
                return
            _write_json(req, structure)
        _safe(req, _do, "/api/skills/structure")
    return handler


# ────────── /api/channels ──────────

def make_channels_handler(
    settings: Any,
    health: HealthState,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            snap = health.snapshot()["components"]
            payload = {
                "repl": {
                    "enabled": True,
                    "status":  "up",  # 进程在跑就算 up
                },
                "web": {
                    "enabled": True,
                    "status":  snap.get("web", "unknown"),
                    "host":    "0.0.0.0",
                    "port":    int(getattr(settings, "web_port", 9527)),
                },
                "wechat": {
                    "enabled": bool(getattr(settings, "wechat_enabled", False)),
                    "status":  snap.get("wechat", "disabled"),
                    "has_official_creds": bool(
                        str(getattr(settings, "weixin_account_id", "")).strip()
                        and getattr(settings, "weixin_token", None)
                    ),
                    "has_webhook_creds": bool(
                        getattr(settings, "ilink_api_key", None)
                        and getattr(settings, "ilink_webhook_secret", None)
                    ),
                },
            }
            _write_json(req, payload)
        _safe(req, _do, "/api/channels")
    return handler


# ────────── /api/permissions ──────────

def make_permissions_handler(
    settings_loader: SettingsLoader | None,
    db: Database,
    loop: asyncio.AbstractEventLoop,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            if settings_loader is None:
                _write_json(req, {
                    "default_mode": "ask",
                    "allow": [], "deny": [],
                    "recent": [], "kpi": {"approved": 0, "denied": 0},
                })
                return
            s = settings_loader.get()
            recent = _run(loop, db.list_recent_permissions(limit=50))
            since_ms = int((time.time() - 24 * 3600) * 1000)
            approved = sum(1 for r in recent if r.get("ts", 0) >= since_ms and r.get("decision") == "allow")
            denied   = sum(1 for r in recent if r.get("ts", 0) >= since_ms and r.get("decision") == "deny")
            _write_json(req, {
                "default_mode": s.default_mode,
                "allow":        list(s.allow),
                "deny":         list(s.deny),
                "source_paths": [str(p) for p in s.source_paths],
                "recent":       recent,
                "kpi":          {"approved": approved, "denied": denied},
            })
        _safe(req, _do, "/api/permissions")
    return handler


def make_settings_json_handler(
    settings_loader: SettingsLoader | None,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            if settings_loader is None:
                _write_json(req, {"path": "", "body": ""})
                return
            project = settings_loader.project_path
            global_ = settings_loader.global_path
            target = project if project.is_file() else global_
            body = ""
            if target.is_file():
                try:
                    body = target.read_text(encoding="utf-8")
                except OSError as exc:
                    body = f"// 读失败：{exc}"
            _write_json(req, {
                "path":         str(target),
                "project_path": str(project),
                "global_path":  str(global_),
                "body":         body,
            })
        _safe(req, _do, "/api/settings_json")
    return handler
