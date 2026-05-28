"""dashboard 写端点；POST/PUT/DELETE。

约定：返回 {"ok": true, ...} 或 {"error": "..."}（HTTP 状态码也设对）。
所有 endpoint 都做基础校验：路径不能含 .. 和 /；JSON 必须能解析。
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sanshiliu.channels.web.api import resolve_persona_file
from sanshiliu.channels.web.handlers import SessionStore
from sanshiliu.foundation.logging import get_logger
from sanshiliu.identity.loader import PersonaLoader
from sanshiliu.memory.longterm.claudemd import ClaudeMdLoader
from sanshiliu.memory.longterm.memdir import MemdirLoader, write_memory_file
from sanshiliu.memory.types import MEMORY_TYPES, MemoryEntry
from sanshiliu.security.settings_loader import SettingsLoader
from sanshiliu.skills.loader import SkillLoader
from sanshiliu.skills.structure import skill_structure_path

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler

    from sanshiliu.storage.db import Database

_logger = get_logger(__name__)


# ────────── 工具 ──────────

def _read_json(req: BaseHTTPRequestHandler) -> dict[str, Any] | None:
    length = int(req.headers.get("Content-Length", "0") or "0")
    if length <= 0 or length > 1024 * 1024:
        return None
    try:
        parsed = json.loads(req.rfile.read(length).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _write_json(req: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    req.send_response(status)
    req.send_header("Content-Type", "application/json; charset=utf-8")
    req.send_header("Content-Length", str(len(body)))
    req.end_headers()
    req.wfile.write(body)


def _safe_filename(name: str) -> bool:
    return bool(name) and "/" not in name and "\\" not in name and ".." not in name


def _safe_session_id(session_id: str) -> bool:
    return (
        bool(session_id)
        and len(session_id) <= 256
        and "/" not in session_id
        and "\\" not in session_id
        and ".." not in session_id
    )


def _session_jsonl_path(data_dir: Path, session_id: str) -> Path:
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")[:120] or "default"
    return data_dir / "sessions" / f"{safe}.jsonl"


# ────────── POST /api/sessions/new ──────────

def make_session_new_handler(
    session_store: SessionStore,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        body = _read_json(req) or {}
        channel = str(body.get("channel") or "web")
        sess = session_store.get_or_create(None, channel=channel)
        _write_json(req, {"ok": True, "session_id": sess.session_id, "channel": sess.channel})

    return handler


# ────────── DELETE /api/sessions/{id} ──────────

def make_session_delete_handler(
    db: Database,
    loop: asyncio.AbstractEventLoop,
    session_store: SessionStore,
    data_dir: Path,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        raw = req.path.split("?", 1)[0]
        prefix = "/api/sessions/"
        if not raw.startswith(prefix):
            _write_json(req, {"error": "bad path"}, status=400)
            return
        rest = raw[len(prefix):]
        if rest.endswith("/"):
            rest = rest[:-1]
        if not rest or "/" in rest:
            _write_json(req, {"error": "bad path"}, status=400)
            return

        session_id = urllib.parse.unquote(rest)
        if not _safe_session_id(session_id):
            _write_json(req, {"error": "invalid session_id"}, status=400)
            return

        try:
            fut = asyncio.run_coroutine_threadsafe(db.delete_session(session_id), loop)
            deleted = fut.result(timeout=10.0)
        except Exception as exc:
            _logger.error("删除会话数据库记录失败", session_id=session_id, error=str(exc))
            _write_json(req, {"error": str(exc)}, status=500)
            return

        memory_deleted = session_store.delete(session_id)
        jsonl_deleted = False
        path = _session_jsonl_path(data_dir, session_id)
        try:
            if path.is_file():
                path.unlink()
                jsonl_deleted = True
        except OSError as exc:
            _logger.error("删除会话 jsonl 失败", session_id=session_id, path=str(path), error=str(exc))
            _write_json(req, {"error": str(exc)}, status=500)
            return

        _write_json(req, {
            "ok": True,
            "session_id": session_id,
            "deleted": deleted,
            "memory": memory_deleted,
            "jsonl": jsonl_deleted,
        })

    return handler


# ────────── PUT /api/persona/{filename} ──────────

def make_persona_write_handler(
    persona_loader: PersonaLoader,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        raw = req.path.split("?", 1)[0]
        parts = raw.strip("/").split("/")
        if len(parts) < 3:
            _write_json(req, {"error": "bad path"}, status=400); return
        fname = urllib.parse.unquote(parts[2])
        if not _safe_filename(fname) or not fname.endswith(".md"):
            _write_json(req, {"error": "invalid filename"}, status=400); return
        body = _read_json(req)
        if body is None or "body" not in body:
            _write_json(req, {"error": "missing body"}, status=400); return
        # 已存在的文件就地写（不论 core/ 还是 modules/）；不存在默认落 core/
        path = resolve_persona_file(persona_loader, fname) or (persona_loader.core_dir / fname)
        is_core = path.parent.resolve() == persona_loader.core_dir.resolve()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(body["body"]), encoding="utf-8")
            # core/ 下的改动由 watcher 5s 内 invalidate；这里只对 core 同步触发一次重读，保证立即一致
            if is_core:
                persona_loader.invalidate()
                persona_loader.load()
        except OSError as exc:
            _write_json(req, {"error": str(exc)}, status=500); return
        _write_json(req, {"ok": True, "path": str(path), "chars": len(str(body["body"]))})

    return handler


# ────────── POST /api/memory ──────────

def make_memory_create_handler(
    memdir_loader: MemdirLoader,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        body = _read_json(req)
        if not body:
            _write_json(req, {"error": "missing body"}, status=400); return
        name = str(body.get("name") or "").strip()
        desc = str(body.get("description") or "").strip()
        mtype = str(body.get("type") or "user").strip()
        text = str(body.get("body") or "")
        if not name or not desc:
            _write_json(req, {"error": "name/description 不能为空"}, status=400); return
        if mtype not in MEMORY_TYPES:
            _write_json(req, {"error": f"type 必须是 {MEMORY_TYPES}"}, status=400); return
        try:
            entry = MemoryEntry(
                name=name, description=desc, memory_type=mtype,
                body=text, file_path=Path(),
            )
            path = write_memory_file(memdir_loader.root, entry, body=text)
            memdir_loader.invalidate()
            memdir_loader.load()
        except Exception as exc:
            _write_json(req, {"error": str(exc)}, status=500); return
        _write_json(req, {"ok": True, "path": path.name})

    return handler


# ────────── PUT/DELETE /api/memory/{file} ──────────

def make_memory_modify_handler(
    memdir_loader: MemdirLoader,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        raw = req.path.split("?", 1)[0]
        rest = raw[len("/api/memory/"):]
        fname = urllib.parse.unquote(rest)
        if not _safe_filename(fname) or not fname.endswith(".md"):
            _write_json(req, {"error": "invalid filename"}, status=400); return
        path = memdir_loader.root / fname
        method = req.command.upper()
        if method == "DELETE":
            if not path.is_file():
                _write_json(req, {"error": "not found"}, status=404); return
            try:
                path.unlink()
                _rebuild_memdir_index(memdir_loader)
                memdir_loader.invalidate()
                memdir_loader.load()
            except OSError as exc:
                _write_json(req, {"error": str(exc)}, status=500); return
            _write_json(req, {"ok": True, "deleted": fname})
            return
        # PUT
        body = _read_json(req)
        if body is None or "body" not in body:
            _write_json(req, {"error": "missing body"}, status=400); return
        try:
            path.write_text(str(body["body"]), encoding="utf-8")
            memdir_loader.invalidate()
            memdir_loader.load()
        except OSError as exc:
            _write_json(req, {"error": str(exc)}, status=500); return
        _write_json(req, {"ok": True, "path": fname, "chars": len(str(body["body"]))})

    return handler


def _rebuild_memdir_index(memdir_loader: MemdirLoader) -> None:
    """删除后重建 MEMORY.md 索引；委托 memdir.rebuild_index_file（权威、与写入路径统一格式）。"""
    from sanshiliu.memory.longterm.memdir import rebuild_index_file
    memdir_loader.invalidate()
    snap = memdir_loader.load()
    rebuild_index_file(memdir_loader.root, snap.entries)


# ────────── POST /api/skills/reload ──────────

def make_skills_reload_handler(
    skill_loader: SkillLoader | None,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        if skill_loader is None:
            _write_json(req, {"error": "skills disabled"}, status=400)
            return
        skill_loader.invalidate()
        skills = skill_loader.load()
        _write_json(req, {
            "ok": True,
            "count": len(skills),
            "ids": [s.id for s in skills],
            "structure_files": {s.id: str(skill_structure_path(s)) for s in skills},
        })

    return handler


# ────────── PUT /api/settings_json ──────────

def make_settings_json_write_handler(
    settings_loader: SettingsLoader | None,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        if settings_loader is None:
            _write_json(req, {"error": "security disabled"}, status=400); return
        body = _read_json(req)
        if body is None or "body" not in body:
            _write_json(req, {"error": "missing body"}, status=400); return
        text = str(body["body"])
        try:
            json.loads(text)  # 校验
        except json.JSONDecodeError as exc:
            _write_json(req, {"error": f"JSON 不合法：{exc}"}, status=400); return
        path = settings_loader.project_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        settings_loader.invalidate()
        settings_loader.load()
        _write_json(req, {"ok": True, "path": str(path)})

    return handler


# ────────── PUT /api/permissions/default_mode ──────────

def make_permissions_default_mode_handler(
    settings_loader: SettingsLoader | None,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        if settings_loader is None:
            _write_json(req, {"error": "security disabled"}, status=400); return
        body = _read_json(req)
        if body is None or "default_mode" not in body:
            _write_json(req, {"error": "missing default_mode"}, status=400); return
        mode = str(body["default_mode"])
        if mode not in ("allow", "deny", "ask"):
            _write_json(req, {"error": "default_mode 必须是 allow/deny/ask"}, status=400); return
        _edit_settings(settings_loader, lambda d: _set_default_mode(d, mode))
        _write_json(req, {"ok": True, "default_mode": mode})

    return handler


# ────────── POST/DELETE /api/permissions/rule ──────────

def make_permissions_rule_handler(
    settings_loader: SettingsLoader | None,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        if settings_loader is None:
            _write_json(req, {"error": "security disabled"}, status=400); return
        body = _read_json(req) or {}
        group = str(body.get("group") or "")
        pattern = str(body.get("pattern") or "").strip()
        if group not in ("allow", "deny"):
            _write_json(req, {"error": "group 必须是 allow/deny"}, status=400); return
        if not pattern:
            _write_json(req, {"error": "pattern 不能为空"}, status=400); return
        method = req.command.upper()
        if method == "POST":
            _edit_settings(settings_loader, lambda d: _add_rule(d, group, pattern))
            _write_json(req, {"ok": True, "added": pattern})
        elif method == "DELETE":
            _edit_settings(settings_loader, lambda d: _del_rule(d, group, pattern))
            _write_json(req, {"ok": True, "removed": pattern})
        else:
            _write_json(req, {"error": "bad method"}, status=405)

    return handler


# ────────── POST /api/instance/reload ──────────

def make_instance_reload_handler(
    persona_loader: PersonaLoader | None,
    memdir_loader: MemdirLoader | None,
    claudemd_loader: ClaudeMdLoader | None,
    skill_loader: SkillLoader | None,
    settings_loader: SettingsLoader | None,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        results = {}
        for name, ld in [
            ("persona", persona_loader),
            ("memdir", memdir_loader),
            ("claudemd", claudemd_loader),
            ("skills", skill_loader),
            ("settings", settings_loader),
        ]:
            if ld is None:
                results[name] = "disabled"
                continue
            try:
                ld.invalidate()
                ld.load()
                results[name] = "reloaded"
            except Exception as exc:
                results[name] = f"error: {exc}"
        _write_json(req, {"ok": True, "results": results})

    return handler


# ────────── settings.json 编辑辅助 ──────────

def _read_settings_dict(path: Path) -> dict[str, Any]:
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def _edit_settings(
    settings_loader: SettingsLoader,
    mutator: Callable[[dict[str, Any]], None],
) -> None:
    path = settings_loader.project_path
    data = _read_settings_dict(path)
    mutator(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    settings_loader.invalidate()
    settings_loader.load()


def _set_default_mode(d: dict[str, Any], mode: str) -> None:
    perms = d.setdefault("permissions", {})
    if not isinstance(perms, dict):
        perms = d["permissions"] = {}
    perms["defaultMode"] = mode


def _add_rule(d: dict[str, Any], group: str, pattern: str) -> None:
    perms = d.setdefault("permissions", {})
    if not isinstance(perms, dict):
        perms = d["permissions"] = {}
    lst = perms.setdefault(group, [])
    if not isinstance(lst, list):
        lst = perms[group] = []
    if pattern not in lst:
        lst.append(pattern)


def _del_rule(d: dict[str, Any], group: str, pattern: str) -> None:
    perms = d.get("permissions")
    if not isinstance(perms, dict):
        return
    lst = perms.get(group)
    if not isinstance(lst, list):
        return
    while pattern in lst:
        lst.remove(pattern)
