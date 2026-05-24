"""dashboard 设置页面读写端点；GET 返回脱敏视图，PUT 持久化到 .env。

设计要点：
- 敏感字段（api_key/token/password）回 dashboard 时只给 *_set 标志和 *_masked 预览；
- PUT 请求里若某个敏感字段缺失或为空字符串则表示"不修改"；
- .env 编辑保留注释与未涉及行，按 KEY=VALUE 行就地替换，缺则追加；
- 提示用户：LLM/通道/密码等配置修改后多数字段需要重启进程方能生效。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sanshiliu.foundation.logging import get_logger

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler

_logger = get_logger(__name__)

# 暴露给 dashboard 的字段集合；与 foundation/config.py 中字段名一一对应
_PLAIN_FIELDS: tuple[tuple[str, str], ...] = (
    # (env_key, display_key)
    ("OPENAI_BASE_URL",            "openai_base_url"),
    ("OPENAI_MODEL",               "openai_model"),
    ("SANSHILIU_WECHAT_ENABLED",   "wechat_enabled"),
    ("WEIXIN_ACCOUNT_ID",          "weixin_account_id"),
    ("WEIXIN_BASE_URL",            "weixin_base_url"),
    ("ILINK_BASE_URL",             "ilink_base_url"),
)

_SECRET_FIELDS: tuple[tuple[str, str], ...] = (
    ("OPENAI_API_KEY",             "openai_api_key"),
    ("WEIXIN_TOKEN",               "weixin_token"),
    ("ILINK_API_KEY",              "ilink_api_key"),
    ("ILINK_WEBHOOK_SECRET",       "ilink_webhook_secret"),
    ("SANSHILIU_DASHBOARD_PASSWORD", "dashboard_password"),
)

# 布尔字段：env 文件里写 "true" / "false"
_BOOL_FIELDS = {"SANSHILIU_WECHAT_ENABLED"}


# ────────── 工具 ──────────

def _read_json(req: BaseHTTPRequestHandler) -> dict[str, Any] | None:
    length = int(req.headers.get("Content-Length", "0") or "0")
    if length <= 0 or length > 256 * 1024:
        return None
    try:
        return json.loads(req.rfile.read(length).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _write_json(req: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    req.send_response(status)
    req.send_header("Content-Type", "application/json; charset=utf-8")
    req.send_header("Content-Length", str(len(body)))
    req.end_headers()
    req.wfile.write(body)


def _mask(value: str) -> str:
    """把敏感字段做*星号脱敏；保留头 4 / 尾 2 字符。"""
    if not value:
        return ""
    if len(value) <= 6:
        return "***"
    return f"{value[:4]}***{value[-2:]}"


def _parse_env_file(path: Path) -> dict[str, str]:
    """读 .env 为 dict；忽略注释与空行；仅取最简 KEY=VAL。"""
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # 去除两侧引号（不递归 escape；项目里都是简单值）
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            out[key] = value
    except OSError as exc:
        _logger.warning(".env 读失败", path=str(path), error=str(exc))
    return out


def _write_env_file(path: Path, updates: dict[str, str | None]) -> None:
    """就地更新 .env：key 已存在则替换该行，否则追加到文件末尾。
    value 为 None 表示删除该行。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_lines: list[str] = []
    if path.is_file():
        existing_lines = path.read_text(encoding="utf-8").splitlines()

    pending = dict(updates)
    new_lines: list[str] = []
    pat = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")

    for line in existing_lines:
        m = pat.match(line)
        if m and m.group(1) in pending:
            key = m.group(1)
            value = pending.pop(key)
            if value is None:
                continue  # 删除
            new_lines.append(f"{key}={value}")
        else:
            new_lines.append(line)

    # 把还没写到的新 key 追加
    if pending:
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        new_lines.append("# Added by dashboard settings page")
        for key, value in pending.items():
            if value is None:
                continue
            new_lines.append(f"{key}={value}")

    body = "\n".join(new_lines)
    if not body.endswith("\n"):
        body += "\n"
    path.write_text(body, encoding="utf-8")


# ────────── GET /api/settings ──────────

def make_get_settings_handler(
    env_path: Path,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        try:
            env = _parse_env_file(env_path)
            payload: dict[str, Any] = {
                "env_path": str(env_path),
                "values": {},
                "secrets": {},
            }
            for env_key, disp_key in _PLAIN_FIELDS:
                raw = env.get(env_key, "")
                if env_key in _BOOL_FIELDS:
                    payload["values"][disp_key] = raw.strip().lower() in ("1", "true", "yes", "on")
                else:
                    payload["values"][disp_key] = raw
            for env_key, disp_key in _SECRET_FIELDS:
                raw = env.get(env_key, "")
                payload["secrets"][disp_key] = {
                    "set":    bool(raw),
                    "masked": _mask(raw),
                }
            _write_json(req, payload)
        except Exception as exc:
            _logger.exception("/api/settings GET 失败", error=str(exc))
            _write_json(req, {"error": str(exc)}, status=500)

    return handler


# ────────── PUT /api/settings ──────────

def make_put_settings_handler(
    env_path: Path,
) -> Callable[[BaseHTTPRequestHandler], None]:
    def handler(req: BaseHTTPRequestHandler) -> None:
        body = _read_json(req)
        if body is None:
            _write_json(req, {"error": "invalid JSON"}, status=400); return

        updates: dict[str, str | None] = {}
        applied: list[str] = []

        # 普通字段：直接读 value，空字符串也视作"清空"
        for env_key, disp_key in _PLAIN_FIELDS:
            if disp_key not in body:
                continue
            raw = body[disp_key]
            if env_key in _BOOL_FIELDS:
                updates[env_key] = "true" if bool(raw) else "false"
            else:
                if raw is None:
                    updates[env_key] = ""
                else:
                    text = str(raw).strip()
                    if any(ch in text for ch in ("\n", "\r")):
                        _write_json(req, {"error": f"{disp_key} 不能包含换行"}, status=400); return
                    updates[env_key] = text
            applied.append(disp_key)

        # 敏感字段：缺失或空字符串 = 不修改；非空才更新
        for env_key, disp_key in _SECRET_FIELDS:
            if disp_key not in body:
                continue
            raw = body[disp_key]
            if raw is None:
                continue
            text = str(raw)
            if not text.strip():
                continue
            if any(ch in text for ch in ("\n", "\r")):
                _write_json(req, {"error": f"{disp_key} 不能包含换行"}, status=400); return
            updates[env_key] = text.strip()
            applied.append(disp_key)

        if not updates:
            _write_json(req, {"ok": True, "applied": [], "note": "无字段变更"}); return

        try:
            _write_env_file(env_path, updates)
        except OSError as exc:
            _write_json(req, {"error": f"写入 .env 失败：{exc}"}, status=500); return

        _write_json(req, {
            "ok": True,
            "applied": applied,
            "env_path": str(env_path),
            "note": "已写入 .env；多数字段需要重启进程方能生效",
        })

    return handler
