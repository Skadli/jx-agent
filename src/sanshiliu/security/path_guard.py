"""路径白名单 / 黑名单；file_read / file_write 等路径参数的安全网。

设计：默认拒绝若干敏感路径（~/.ssh, /etc, ~/.aws, ~/.gnupg），
settings.json 的 Read(<glob>) / Write(<glob>) 可以追加 allow/deny。
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterable
from pathlib import Path

# 默认拒绝 glob；用户 ~ 由 expanduser 展开，平台无关写法
_DEFAULT_DENY_GLOBS: tuple[str, ...] = (
    "~/.ssh/**",
    "~/.ssh/*",
    "~/.aws/**",
    "~/.gnupg/**",
    "~/.config/gh/hosts.yml",
    "/etc/shadow",
    "/etc/sudoers",
    "/etc/sudoers.d/**",
)


def _expand(glob: str) -> str:
    """把 ~ 展开为绝对路径；不做 resolve 以保留 glob 通配符。"""
    return os.path.expanduser(glob)


def _normalize(path: str | Path) -> str:
    """把路径归一化成 forward-slash 绝对字符串，便于 fnmatch 匹配 glob。"""
    p = Path(path).expanduser()
    try:
        # resolve(strict=False)：不要求路径存在
        p = p.resolve(strict=False)
    except OSError:
        p = p.absolute()
    return p.as_posix()


def _match_globs(path_str: str, globs: Iterable[str]) -> str | None:
    """命中任意一个 glob 时返回该 glob；fnmatch 支持 *, **, ?, [...]"""
    for g in globs:
        expanded = _expand(g).replace("\\", "/")
        # fnmatch 不原生支持 **；这里把 ** 折成 * 后再判，足够覆盖深层路径
        flat = expanded.replace("/**", "/*").replace("**", "*")
        if fnmatch.fnmatch(path_str, expanded) or fnmatch.fnmatch(path_str, flat):
            return g
    return None


class PathGuard:
    """路径校验器；与 PermissionManager 协同，独立可测。"""

    def __init__(
        self,
        *,
        cwd_root: Path,
        extra_allow_globs: Iterable[str] = (),
        extra_deny_globs: Iterable[str] = (),
        defaults_enabled: bool = True,
    ) -> None:
        self._cwd_root = cwd_root.expanduser().resolve()
        self._allow_globs = tuple(extra_allow_globs)
        deny: list[str] = list(extra_deny_globs)
        if defaults_enabled:
            for g in _DEFAULT_DENY_GLOBS:
                if g not in deny:
                    deny.append(g)
        self._deny_globs = tuple(deny)

    @property
    def cwd_root(self) -> Path:
        return self._cwd_root

    @property
    def deny_globs(self) -> tuple[str, ...]:
        return self._deny_globs

    @property
    def allow_globs(self) -> tuple[str, ...]:
        return self._allow_globs

    def check(self, raw_path: str) -> str | None:
        """命中 deny → 返回触发 glob；命中 allow 或 cwd 内 → None；其余 → 'out-of-cwd'。"""
        if not raw_path:
            return "empty-path"
        norm = _normalize(raw_path)

        # 1) 明示 deny 优先
        hit = _match_globs(norm, self._deny_globs)
        if hit:
            return hit

        # 2) 明示 allow 命中即放行
        if _match_globs(norm, self._allow_globs):
            return None

        # 3) cwd_root 内默认 allow
        try:
            Path(norm).relative_to(self._cwd_root)
            return None
        except ValueError:
            return "out-of-cwd"
