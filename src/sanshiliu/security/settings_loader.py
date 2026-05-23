"""settings.json 加载器；与 Claude 协议一致；项目级覆盖全局级。

R9（风险登记）：解析失败 fail-open 到 defaultMode="ask" 并告警，不阻塞启动。
"""

from __future__ import annotations

import json
from pathlib import Path

from sanshiliu.foundation.logging import get_logger
from sanshiliu.security.types import (
    DEFAULT_MODES,
    DefaultMode,
    PermissionSettings,
)

_logger = get_logger(__name__)

_SETTINGS_FILENAME = "settings.json"


def _read_one(path: Path) -> dict | None:
    """读单个 settings.json；缺/坏文件返回 None 不抛。"""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("settings.json 解析失败（已跳过，fail-open 到 ask）",
                        path=str(path), error=str(exc))
        return None
    if not isinstance(data, dict):
        _logger.warning("settings.json 顶层不是对象（已跳过）", path=str(path))
        return None
    return data


def _coerce_list(raw: object) -> list[str]:
    """settings.json 中 allow/deny 字段强制成字符串列表。"""
    if not isinstance(raw, list):
        return []
    return [str(x) for x in raw if isinstance(x, str) and x.strip()]


def _coerce_default_mode(raw: object) -> DefaultMode:
    """defaultMode 强制成合法字面量；非法→ask。"""
    if isinstance(raw, str) and raw in DEFAULT_MODES:
        return raw  # type: ignore[return-value]
    return "ask"


def _merge(global_data: dict | None, project_data: dict | None) -> tuple[DefaultMode, list[str], list[str]]:
    """合并两份 permissions：项目级覆盖 defaultMode；allow/deny 拼接去重保序。"""
    g_perm = (global_data or {}).get("permissions") or {}
    p_perm = (project_data or {}).get("permissions") or {}
    if not isinstance(g_perm, dict):
        g_perm = {}
    if not isinstance(p_perm, dict):
        p_perm = {}

    # defaultMode：项目优先；都没有 → ask
    default_mode = _coerce_default_mode(
        p_perm.get("defaultMode", g_perm.get("defaultMode", "ask"))
    )

    allow_list: list[str] = []
    for src in (_coerce_list(g_perm.get("allow")), _coerce_list(p_perm.get("allow"))):
        for item in src:
            if item not in allow_list:
                allow_list.append(item)

    deny_list: list[str] = []
    for src in (_coerce_list(g_perm.get("deny")), _coerce_list(p_perm.get("deny"))):
        for item in src:
            if item not in deny_list:
                deny_list.append(item)

    return default_mode, allow_list, deny_list


class SettingsLoader:
    """两份 settings.json 的合并加载器；支持运行时 reload。"""

    def __init__(self, *, global_home: Path, project_cwd: Path) -> None:
        self._global_path = global_home / _SETTINGS_FILENAME
        self._project_path = project_cwd / _SETTINGS_FILENAME
        self._cache: PermissionSettings | None = None

    @property
    def project_path(self) -> Path:
        return self._project_path

    @property
    def global_path(self) -> Path:
        return self._global_path

    def load(self) -> PermissionSettings:
        g = _read_one(self._global_path)
        p = _read_one(self._project_path)
        mode, allow, deny = _merge(g, p)
        sources = tuple(
            path for path, data in ((self._global_path, g), (self._project_path, p)) if data is not None
        )
        snap = PermissionSettings(
            default_mode=mode, allow=tuple(allow), deny=tuple(deny), source_paths=sources,
        )
        self._cache = snap
        _logger.info(
            "settings.json 加载",
            default_mode=mode, allow_count=len(allow), deny_count=len(deny),
            sources=[str(p) for p in sources],
        )
        return snap

    def get(self) -> PermissionSettings:
        return self._cache if self._cache is not None else self.load()

    def invalidate(self) -> None:
        self._cache = None


def append_allow_pattern(project_path: Path, pattern: str) -> None:
    """把一条 pattern 追加到项目级 settings.json 的 permissions.allow；幂等。

    8-V3：用户选择 "always" 时写盘；不存在就建新文件，保持 JSON 缩进风格。
    """
    data: dict = {}
    if project_path.is_file():
        try:
            text = project_path.read_text(encoding="utf-8")
            loaded = json.loads(text) if text.strip() else {}
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError) as exc:
            _logger.warning("settings.json 读失败，将覆写", path=str(project_path), error=str(exc))
            data = {}

    perms = data.get("permissions")
    if not isinstance(perms, dict):
        perms = {}
        data["permissions"] = perms
    allow = perms.get("allow")
    if not isinstance(allow, list):
        allow = []
        perms["allow"] = allow
    if pattern not in allow:
        allow.append(pattern)

    project_path.parent.mkdir(parents=True, exist_ok=True)
    project_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    _logger.info("settings.json 已写入 allow pattern", pattern=pattern, path=str(project_path))
