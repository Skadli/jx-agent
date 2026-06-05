"""dashboard 写端点；POST/PUT/DELETE。

约定：返回 {"ok": true, ...} 或 {"error": "..."}（HTTP 状态码也设对）。
所有 endpoint 都做基础校验：路径不能含 .. 和 /；JSON 必须能解析。
"""

from __future__ import annotations

import asyncio
import json
import shutil
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
from sanshiliu.memory.types import (
    MEMORY_APPLIES,
    MEMORY_TYPES,
    MemoryEntry,
)
from sanshiliu.scheduler.growth_persona import chapter_persona_dir
from sanshiliu.scheduler.growth_state import (
    load_growth_state,
    save_growth_state,
    seed_growth_state,
)
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
            _write_json(req, {"error": "bad path"}, status=400)
            return
        fname = urllib.parse.unquote(parts[2])
        if not _safe_filename(fname) or not fname.endswith(".md"):
            _write_json(req, {"error": "invalid filename"}, status=400)
            return
        body = _read_json(req)
        if body is None or "body" not in body:
            _write_json(req, {"error": "missing body"}, status=400)
            return
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
            _write_json(req, {"error": str(exc)}, status=500)
            return
        _write_json(req, {"ok": True, "path": str(path), "chars": len(str(body["body"]))})

    return handler


# ────────── POST /api/memory ──────────

def make_memory_create_handler(
    memdir_loader: MemdirLoader,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        body = _read_json(req)
        if not body:
            _write_json(req, {"error": "missing body"}, status=400)
            return
        name = str(body.get("name") or "").strip()
        desc = str(body.get("description") or "").strip()
        mtype = str(body.get("type") or "user").strip()
        apply = str(body.get("apply") or "").strip().lower() or None
        text = str(body.get("body") or "")
        if not name or not desc:
            _write_json(req, {"error": "name/description 不能为空"}, status=400)
            return
        if mtype not in MEMORY_TYPES:
            _write_json(req, {"error": f"type 必须是 {MEMORY_TYPES}"}, status=400)
            return
        if apply is not None and apply not in MEMORY_APPLIES:
            _write_json(req, {"error": f"apply 必须是 {MEMORY_APPLIES}"}, status=400)
            return
        # mypy 已由上面的 `not in MEMORY_TYPES` / `not in MEMORY_APPLIES` 守卫收窄类型，无需 cast
        memory_type = mtype
        memory_apply = apply if apply is not None else None
        try:
            entry = MemoryEntry(
                name=name, description=desc, memory_type=memory_type,
                body=text, apply=memory_apply, file_path=Path(),
            )
            path = write_memory_file(memdir_loader.root, entry, body=text)
            memdir_loader.invalidate()
            memdir_loader.load()
        except Exception as exc:
            _write_json(req, {"error": str(exc)}, status=500)
            return
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
            _write_json(req, {"error": "invalid filename"}, status=400)
            return
        path = memdir_loader.root / fname
        method = req.command.upper()
        if method == "DELETE":
            if not path.is_file():
                _write_json(req, {"error": "not found"}, status=404)
                return
            try:
                path.unlink()
                _rebuild_memdir_index(memdir_loader)
                memdir_loader.invalidate()
                memdir_loader.load()
            except OSError as exc:
                _write_json(req, {"error": str(exc)}, status=500)
                return
            _write_json(req, {"ok": True, "deleted": fname})
            return
        # PUT
        body = _read_json(req)
        if body is None or "body" not in body:
            _write_json(req, {"error": "missing body"}, status=400)
            return
        try:
            path.write_text(str(body["body"]), encoding="utf-8")
            memdir_loader.invalidate()
            memdir_loader.load()
        except OSError as exc:
            _write_json(req, {"error": str(exc)}, status=500)
            return
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
            _write_json(req, {"error": "security disabled"}, status=400)
            return
        body = _read_json(req)
        if body is None or "body" not in body:
            _write_json(req, {"error": "missing body"}, status=400)
            return
        text = str(body["body"])
        try:
            json.loads(text)  # 校验
        except json.JSONDecodeError as exc:
            _write_json(req, {"error": f"JSON 不合法：{exc}"}, status=400)
            return
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
            _write_json(req, {"error": "security disabled"}, status=400)
            return
        body = _read_json(req)
        if body is None or "default_mode" not in body:
            _write_json(req, {"error": "missing default_mode"}, status=400)
            return
        mode = str(body["default_mode"])
        if mode not in ("allow", "deny", "ask"):
            _write_json(req, {"error": "default_mode 必须是 allow/deny/ask"}, status=400)
            return
        _edit_settings(settings_loader, lambda d: _set_default_mode(d, mode))
        _write_json(req, {"ok": True, "default_mode": mode})

    return handler


# ────────── POST/DELETE /api/permissions/rule ──────────

def make_permissions_rule_handler(
    settings_loader: SettingsLoader | None,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        if settings_loader is None:
            _write_json(req, {"error": "security disabled"}, status=400)
            return
        body = _read_json(req) or {}
        group = str(body.get("group") or "")
        pattern = str(body.get("pattern") or "").strip()
        if group not in ("allow", "deny"):
            _write_json(req, {"error": "group 必须是 allow/deny"}, status=400)
            return
        if not pattern:
            _write_json(req, {"error": "pattern 不能为空"}, status=400)
            return
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


# ────────── DELETE /api/growth/chapters/{n} ──────────

def make_growth_chapter_delete_handler(
    growth_state_path: Path,
    *,
    start_age: int,
    years_per_chapter: int,
    end_age: int,
    data_dir: Path,
    memdir_loader: MemdirLoader | None,
    persona_loader: PersonaLoader | None,
    growth_running: Callable[[], bool] | None = None,
) -> Callable[[BaseHTTPRequestHandler], None]:
    """删第 n 章及其后所有章（成长是连续时间线，不能留空洞）。删 1 = 清空全部。

    一并清理：传记 md + 人格快照目录 data/growth/persona/chapter-N/（#2），并令人格回退即时生效。
    清空到 0 章时按当前 config 重新 seed cadence（#1：把旧 1 年/章迁到现默认 5 年/章）。
    成长心跳任务正在跑时返回 409（#3）——它也在 load→改→save 同一状态文件，并发会丢更新。
    已装外部 skill 不卸载（二期，与 rollback 一致）。
    """
    def handler(req: BaseHTTPRequestHandler) -> None:
        raw = req.path.split("?", 1)[0]
        prefix = "/api/growth/chapters/"
        if not raw.startswith(prefix):
            _write_json(req, {"error": "bad path"}, status=400)
            return
        token = urllib.parse.unquote(raw[len(prefix):].strip("/"))
        # 只认 ASCII 十进制正整数（同 api_growth._parse_chapter_no：挡全角/上标/.. 注入）
        if not (token.isascii() and token.isdecimal()):
            _write_json(req, {"error": "invalid chapter number"}, status=400)
            return
        n = int(token)
        if n <= 0:  # 与 GET 端点一致：非正章号是非法请求(400)，而非"找不到"(404)
            _write_json(req, {"error": "invalid chapter number"}, status=400)
            return
        # #3：成长任务正在跑时拒绝——它也在 load→改→save 同一个 growth-state.json，并发会丢更新
        if growth_running is not None and growth_running():
            _write_json(
                req,
                {"error": "成长任务正在运行，请稍后再删", "code": "growth-busy"},
                status=409,
            )
            return
        state = load_growth_state(
            growth_state_path,
            start_age=start_age,
            years_per_chapter=years_per_chapter,
            end_age=end_age,
        )
        if n > state.current_chapter:
            _write_json(
                req,
                {"error": "chapter not found", "chapter_no": n, "completed": state.current_chapter},
                status=404,
            )
            return
        removed = state.delete_from(n)
        if state.current_chapter == 0:
            # #1：清空到起点 → 按当前 config 重新 seed，否则旧 years_per_chapter/end_chapter 一直粘着、
            # 和现默认/文档（5 年/章）不一致。等价于删掉状态文件重建。
            state = seed_growth_state(
                start_age=start_age, years_per_chapter=years_per_chapter, end_age=end_age
            )
        save_growth_state(growth_state_path, state)
        # 清被回退章的传记 md + 人格快照目录（否则残留脏数据 / 过期人格）；都 best-effort
        deleted_bios = _delete_growth_biographies(memdir_loader, removed)
        deleted_persona = _delete_growth_persona_dirs(data_dir, removed)
        # 人格回退即时生效：active_persona_chapter 可能已收敛，loader 缓存需手动失效
        if persona_loader is not None:
            persona_loader.invalidate()
        _write_json(req, {
            "ok": True,
            "removed_chapters": removed,
            "current_chapter": state.current_chapter,
            "age": state.age,
            "active_persona_chapter": state.active_persona_chapter,
            "deleted_biographies": deleted_bios,
            "deleted_persona_dirs": deleted_persona,
        })

    return handler


def _delete_growth_biographies(
    memdir_loader: MemdirLoader | None, chapter_nos: list[int]
) -> int:
    """删被回退章的传记 md：reference_growth-chapter-N_*.md（N 后带 _，不会误伤 chapter-10）。

    删完重建 MEMORY.md 索引并 reload。memdir 关闭则返 0；删不动只跳过（best-effort）。
    """
    if memdir_loader is None:
        return 0
    root = memdir_loader.root
    count = 0
    for n in chapter_nos:
        for p in root.glob(f"reference_growth-chapter-{n}_*.md"):
            try:
                p.unlink()
                count += 1
            except OSError:
                pass
    if count:
        _rebuild_memdir_index(memdir_loader)
    return count


def _delete_growth_persona_dirs(data_dir: Path, chapter_nos: list[int]) -> int:
    """删被回退章的人格快照目录 data/growth/persona/chapter-N/（N≥1；chapter-0 起点快照保留）。

    回退/清空后这些目录是孤儿（active 指针已收敛）：留着既是脏数据、又会让
    /api/growth/persona/{N} 读到过期人格。best-effort：删不动只跳过。
    """
    count = 0
    for n in chapter_nos:
        if n <= 0:  # chapter-0 是 5 岁起点快照（= base core），永不随回退删除
            continue
        d = chapter_persona_dir(data_dir, n)
        if d.is_dir():
            try:
                shutil.rmtree(d)
                count += 1
            except OSError:
                pass
    return count


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
