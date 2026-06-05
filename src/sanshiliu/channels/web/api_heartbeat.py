"""Dashboard 心跳任务端点；GET 列表 / POST run / POST toggle。

路径约定：
- GET  /api/heartbeat                  → {"tasks": [...]}
- POST /api/heartbeat/{name}/run       → {"ok": true, "started": bool, "reason": str}
- POST /api/heartbeat/{name}/toggle    → {"ok": true, "enabled": bool}    body {"enabled": bool}
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sanshiliu.foundation.logging import get_logger
from sanshiliu.scheduler import HeartbeatScheduler
from sanshiliu.scheduler.dream_log import load_dream_records

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler

_logger = get_logger(__name__)


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


def _safe_task_name(name: str) -> bool:
    return bool(name) and all(c.isalnum() or c in "-_" for c in name) and len(name) <= 60


def make_heartbeat_list_handler(
    heartbeat: HeartbeatScheduler,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        tasks = [t.to_dict() for t in heartbeat.list_tasks()]
        _write_json(req, {"tasks": tasks})

    return handler


def make_dream_log_handler(
    dream_log_path: Path,
) -> Callable[[BaseHTTPRequestHandler], None]:
    """GET /api/dream/log → {"records": [...]}（最新在前，最多 20 条）。

    做梦不像成长有结构化状态机，这条只读 <data_dir>/dream-log.json（DreamRunner 每次 ok/
    skipped/error 都追加一条），供心跳页回看历史。文件不存在 → records 为空数组。
    """
    def handler(req: BaseHTTPRequestHandler) -> None:
        records = load_dream_records(dream_log_path, limit=20)
        _write_json(req, {"records": records})

    return handler


def make_heartbeat_run_handler(
    heartbeat: HeartbeatScheduler,
    loop: asyncio.AbstractEventLoop,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        name = _parse_task_name(req.path, suffix="/run")
        if name is None:
            _write_json(req, {"error": "bad path"}, status=400)
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(heartbeat.run_now(name), loop)
            started, reason = fut.result(timeout=5.0)
        except Exception as exc:
            _logger.error("heartbeat run 触发失败", name=name, error=str(exc))
            _write_json(req, {"error": str(exc)}, status=500)
            return
        _write_json(req, {"ok": True, "started": started, "reason": reason})

    return handler


def make_heartbeat_toggle_handler(
    heartbeat: HeartbeatScheduler,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        name = _parse_task_name(req.path, suffix="/toggle")
        if name is None:
            _write_json(req, {"error": "bad path"}, status=400)
            return
        body = _read_json(req)
        if body is None or "enabled" not in body or not isinstance(body["enabled"], bool):
            _write_json(req, {"error": "missing/invalid 'enabled' (bool)"}, status=400)
            return
        ok = heartbeat.set_enabled(name, body["enabled"])
        if not ok:
            _write_json(req, {"error": f"task 不存在: {name}"}, status=404)
            return
        _write_json(req, {"ok": True, "enabled": body["enabled"]})

    return handler


def make_heartbeat_config_handler(
    heartbeat: HeartbeatScheduler,
) -> Callable[[BaseHTTPRequestHandler], None]:
    """PUT /api/heartbeat/{name}/config  body: {enabled?, daily_at_hour?, interval_seconds?, extra_params?}"""
    def handler(req: BaseHTTPRequestHandler) -> None:
        name = _parse_task_name(req.path, suffix="/config")
        if name is None:
            _write_json(req, {"error": "bad path"}, status=400)
            return
        body = _read_json(req)
        if body is None:
            _write_json(req, {"error": "missing or invalid JSON"}, status=400)
            return
        ok, reason = heartbeat.update_config(name, body)
        if not ok:
            status = 404 if "不存在" in reason else 400
            _write_json(req, {"error": reason}, status=status)
            return
        task = heartbeat.get(name)
        _write_json(req, {"ok": True, "task": task.to_dict() if task else None})

    return handler


def make_heartbeat_dispatch_handler(
    heartbeat: HeartbeatScheduler,
    loop: asyncio.AbstractEventLoop,
) -> Callable[[BaseHTTPRequestHandler], None]:
    """单一 prefix handler 按尾缀路由：POST /run, POST /toggle, PUT /config。"""
    run_h = make_heartbeat_run_handler(heartbeat, loop)
    toggle_h = make_heartbeat_toggle_handler(heartbeat)
    config_h = make_heartbeat_config_handler(heartbeat)

    def handler(req: BaseHTTPRequestHandler) -> None:
        raw = req.path.split("?", 1)[0]
        if raw.endswith("/run"):
            run_h(req)
        elif raw.endswith("/toggle"):
            toggle_h(req)
        elif raw.endswith("/config"):
            config_h(req)
        else:
            _write_json(req, {"error": "unknown subpath; expect /run, /toggle, or /config"}, status=400)

    return handler


def _parse_task_name(path: str, *, suffix: str) -> str | None:
    """从 /api/heartbeat/<name>/<suffix> 抽 name；非法返 None。"""
    raw = path.split("?", 1)[0]
    prefix = "/api/heartbeat/"
    if not raw.startswith(prefix) or not raw.endswith(suffix):
        return None
    middle = raw[len(prefix) : -len(suffix)]
    if not middle:
        return None
    name = urllib.parse.unquote(middle)
    if not _safe_task_name(name):
        return None
    return name
