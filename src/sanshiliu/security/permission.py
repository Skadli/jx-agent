"""权限状态机；与 Claude settings.json 一致的 allow/deny 模式 + 会话决策缓存。

匹配语法（与 Claude 协议对齐）：
- `Bash(ls:*)`：bash_exec.command 首词 == "ls"（args 任意）
- `Bash(git status)`：完整 command == "git status"
- `Bash(rm:-rf*)`：command 以 "rm -rf" 开头
- `Bash(*)`：任意 bash_exec
- `Read(./**)`：file_read.path 命中 glob
- `WebSearch`：纯工具名匹配（不看 args）

8-V7 兼容：运行时工具名 `bash_exec` ↔ 协议显示名 `Bash`（由 TOOL_ALIASES 映射）。
"""

from __future__ import annotations

import fnmatch
import json
import re
import shlex
from collections.abc import Iterable
from typing import Any

from sanshiliu.foundation.logging import get_logger
from sanshiliu.security.bash_classifier import classify as _classify_bash
from sanshiliu.security.path_guard import PathGuard
from sanshiliu.security.settings_loader import SettingsLoader, append_allow_pattern
from sanshiliu.security.types import (
    Confirmer,
    ConfirmRequest,
    ConfirmResponse,
    DangerLevel,
    DecisionScope,
    PermissionDecision,
    canonical_tool_alias,
)

_logger = get_logger(__name__)

_PATTERN_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)(?:\(([^)]*)\))?$")


def _parse_pattern(raw: str) -> tuple[str, str | None]:
    """`Bash(ls:*)` → ("Bash", "ls:*"); `WebSearch` → ("WebSearch", None)；非法形式→空字符串。"""
    m = _PATTERN_RE.match(raw.strip())
    if not m:
        return "", None
    name = m.group(1)
    inner = m.group(2)
    return name, inner if inner is not None else None


def _match_inner(tool_name: str, inner: str | None, arguments: dict[str, Any]) -> bool:
    """匹配 pattern 的括号内 inner 段；None 视为匹配任意 args。"""
    if inner is None:
        return True

    # 工具的"代表参数"取法
    if tool_name in ("Bash", "bash_exec"):
        command = str(arguments.get("command") or "").strip()
        return _match_bash_inner(command, inner)
    if tool_name in ("Read", "Write", "file_read", "file_write"):
        path = str(arguments.get("path") or "")
        return _match_glob(path, inner)
    # 其他工具：把 inner 当 fnmatch 整体匹配 JSON-args
    flat = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
    return _match_glob(flat, inner)


def _match_glob(s: str, pat: str) -> bool:
    """单层 ** 折叠后用 fnmatch；保持 Claude 风格的"./**"语义。"""
    flat = pat.replace("/**", "/*").replace("**", "*")
    return fnmatch.fnmatch(s, pat) or fnmatch.fnmatch(s, flat)


def _match_bash_inner(command: str, inner: str) -> bool:
    """Bash 内层：`verb`/`verb:argglob`/`full command`/`*`。"""
    if inner == "*" or inner == "":
        return True
    if ":" in inner:
        verb, arg_glob = inner.split(":", 1)
        verb = verb.strip()
        first = _first_word(command)
        if first != verb:
            return False
        if arg_glob == "" or arg_glob == "*":
            return True
        rest = command[len(verb):].lstrip()
        return _match_glob(rest, arg_glob)
    # 完整命令或纯 glob 整体匹配
    if any(ch in inner for ch in "*?["):
        return _match_glob(command, inner)
    return command == inner


def _first_word(command: str) -> str:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    return tokens[0] if tokens else ""


def _pattern_applies_to_tool(pattern_name: str, runtime_tool: str) -> bool:
    """settings pattern 名 vs 运行时工具名；通过 TOOL_ALIASES 互通。"""
    if pattern_name == runtime_tool:
        return True
    alias = canonical_tool_alias(runtime_tool)
    return pattern_name == alias


class PermissionManager:
    """L8 核心；dispatcher 在 execute 前 await check()。"""

    def __init__(
        self,
        *,
        settings_loader: SettingsLoader,
        path_guard: PathGuard | None = None,
        confirmer: Confirmer | None = None,
        db: Any = None,  # storage.db.Database；用 Any 避免循环导入
    ) -> None:
        self._settings_loader = settings_loader
        self._path_guard = path_guard
        self._confirmer = confirmer
        self._db = db
        # 会话级缓存：(session_id, tool_alias, args_fingerprint) → "allow"/"deny"
        self._session_cache: dict[tuple[str, str, str], str] = {}

    # ────────── 主接口 ──────────

    async def check(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        session_id: str,
    ) -> PermissionDecision:
        """权限状态机；返回 allow/deny；ask 路径会内部调 confirmer。"""
        alias = canonical_tool_alias(tool_name)

        # bash 危险级（用于 UI 警示，不直接决策）
        danger: DangerLevel | None = None
        if tool_name in ("bash_exec", "Bash"):
            danger = _classify_bash(str(arguments.get("command") or ""))

        # 路径守卫：对 Read/Write 类先看 path 是否在默认黑名单
        # path_guard_hit 后面 safe-auto 也要看：None 表示完全干净
        path_guard_hit: str | None = None
        if self._path_guard is not None and tool_name in ("file_read", "file_write", "Read", "Write"):
            raw_path = str(arguments.get("path") or "")
            path_guard_hit = self._path_guard.check(raw_path)
            if path_guard_hit and path_guard_hit != "out-of-cwd":
                # 命中默认黑名单 → 直接拒绝
                rule = f"path-guard:{path_guard_hit}"
                _logger.warning("权限拒绝（path-guard）", tool=tool_name, path=raw_path, rule=path_guard_hit)
                return PermissionDecision(
                    kind="deny", rule=rule, danger=danger,
                    reason=f"路径 {raw_path} 命中安全黑名单 {path_guard_hit}",
                )

        settings = self._settings_loader.get()

        # 1) deny pattern
        rule = self._first_match(settings.deny, tool_name, alias, arguments)
        if rule is not None:
            _logger.warning("权限拒绝（settings.deny）", tool=tool_name, rule=rule)
            return PermissionDecision(
                kind="deny", rule=rule, danger=danger,
                reason=f"settings.deny 命中：{rule}",
            )

        # 2) allow pattern
        rule = self._first_match(settings.allow, tool_name, alias, arguments)
        if rule is not None:
            return PermissionDecision(kind="allow", rule=rule, danger=danger)

        # 3) 会话缓存
        fp = _args_fingerprint(arguments)
        cache_key = (session_id, alias, fp)
        cached = self._session_cache.get(cache_key)
        if cached == "allow":
            return PermissionDecision(kind="allow", rule="session-cache", danger=danger)
        if cached == "deny":
            return PermissionDecision(kind="deny", rule="session-cache", danger=danger,
                                      reason="本会话此前已拒绝同款调用")

        # 3.5) 安全工具自动放行；deny / 显式 allow / 会话缓存仍优先，
        # 用户撤销也可以经 settings.deny 收回。覆盖范围：
        #   - bash_exec：classifier 判定 safe（只读/查询类如 ls/git status/cat）
        #   - web_search：纯查询公开 API，无副作用
        #   - file_read：path_guard 完全 clean（在 cwd 且不命中系统黑名单）
        if _is_auto_allowable(tool_name, danger, path_guard_hit):
            rule_label = (
                "bash-safe-auto" if tool_name in ("bash_exec", "Bash")
                else f"{alias.lower()}-safe-auto"
            )
            return PermissionDecision(kind="allow", rule=rule_label, danger=danger)

        # 4) defaultMode
        if settings.default_mode == "allow":
            return PermissionDecision(kind="allow", rule="defaultMode=allow", danger=danger)
        if settings.default_mode == "deny":
            return PermissionDecision(kind="deny", rule="defaultMode=deny", danger=danger,
                                      reason="defaultMode=deny")

        # 5) ask
        if self._confirmer is None:
            return PermissionDecision(
                kind="deny", rule="ask-no-confirmer", danger=danger,
                reason="defaultMode=ask 但本通道没有用户确认能力",
            )

        request = ConfirmRequest(
            tool_name=tool_name,
            canonical_name=alias,
            arguments_preview=_args_preview(arguments),
            danger=danger,
        )
        try:
            response = await self._confirmer.confirm(request)
        except Exception as exc:
            _logger.error("confirmer 异常（fail-closed）", error=str(exc), tool=tool_name)
            return PermissionDecision(
                kind="deny", rule="ask-error", danger=danger,
                reason=f"用户确认流程异常：{exc}",
            )

        await self._apply_response(
            response=response,
            tool_name=tool_name, alias=alias, fingerprint=fp,
            session_id=session_id, arguments=arguments,
        )
        kind = "allow" if response.decision == "allow" else "deny"
        return PermissionDecision(
            kind=kind, rule=f"ask:{response.scope}", danger=danger,
            reason=f"用户确认 {response.decision} / {response.scope}",
        )

    # ────────── 辅助 ──────────

    def _first_match(
        self,
        patterns: Iterable[str],
        tool_name: str,
        alias: str,
        arguments: dict[str, Any],
    ) -> str | None:
        for raw in patterns:
            pname, inner = _parse_pattern(raw)
            if not pname:
                continue
            if not (_pattern_applies_to_tool(pname, tool_name) or pname == alias):
                continue
            if _match_inner(pname, inner, arguments):
                return raw
        return None

    async def _apply_response(
        self,
        *,
        response: ConfirmResponse,
        tool_name: str,
        alias: str,
        fingerprint: str,
        session_id: str,
        arguments: dict[str, Any],
    ) -> None:
        """根据 response.scope 落地：once 不存；session 存内存+DB；permanent 写盘。"""
        scope: DecisionScope = response.scope
        if scope == "once":
            return

        cache_value = response.decision
        if scope in ("session", "permanent"):
            self._session_cache[(session_id, alias, fingerprint)] = cache_value

        if scope == "session" and self._db is not None:
            try:
                await self._db.insert_permission_decision(
                    session_id=session_id,
                    tool_name=alias,
                    decision=response.decision,
                    scope="session",
                    pattern=_pattern_for_decision(alias, arguments),
                )
            except Exception as exc:
                _logger.warning("permission_decisions 写库失败", error=str(exc))

        if scope == "permanent":
            pattern = _pattern_for_decision(alias, arguments)
            try:
                append_allow_pattern(self._settings_loader.project_path, pattern)
                self._settings_loader.invalidate()
            except OSError as exc:
                _logger.warning("settings.json 写入失败", error=str(exc), pattern=pattern)


def _is_auto_allowable(
    tool_name: str,
    danger: DangerLevel | None,
    path_guard_hit: str | None,
) -> bool:
    """无副作用 / 只读类工具自动放行的判定。"""
    # 1. web_search 永远安全（纯查询）
    if tool_name in ("web_search", "WebSearch"):
        return True
    # 2. bash_classifier 判 safe 的 bash
    if tool_name in ("bash_exec", "Bash") and danger == "safe":
        return True
    # 3. file_read 在 cwd 内、没命中任何路径黑名单
    if tool_name in ("file_read", "Read") and path_guard_hit is None:
        return True
    return False


def _args_fingerprint(args: dict[str, Any]) -> str:
    return json.dumps(args, sort_keys=True, ensure_ascii=False)


def _args_preview(args: dict[str, Any]) -> str:
    """单行紧凑预览；超过 200 字截断。"""
    s = json.dumps(args, ensure_ascii=False)
    return s if len(s) <= 200 else s[:200] + "...]"


def _pattern_for_decision(alias: str, arguments: dict[str, Any]) -> str:
    """把一次具体调用反推一条合理 pattern，用于落 settings.json/DB。"""
    if alias == "Bash":
        cmd = str(arguments.get("command") or "").strip()
        verb = _first_word(cmd)
        return f"Bash({verb}:*)" if verb else "Bash(*)"
    if alias in ("Read", "Write"):
        path = str(arguments.get("path") or "")
        return f"{alias}({path})" if path else alias
    return alias
