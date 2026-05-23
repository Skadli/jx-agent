"""Bash 危险命令分类器；移植 Claude BASH_CLASSIFIER 思路。

把命令分到 4 档：safe / moderate / dangerous / critical。
- safe：只读、纯查询（ls / pwd / cat / git status）
- moderate：写入但可逆，或访问外网（npm install / git fetch / curl）
- dangerous：会改文件或仓库状态（rm / mv / git push / git reset）
- critical：可能不可逆 + 系统级（rm -rf / dd / mkfs / shutdown）

1.0 用法：permission 询问用户时把 danger 一并显示；critical 默认拒绝（除非显式 allow）。
"""

from __future__ import annotations

import re
import shlex

from sanshiliu.security.types import DangerLevel

# 命令首词 → 默认危险级；多词组合命中下方 _PATTERNS 后会被升级
_FIRST_WORD: dict[str, DangerLevel] = {
    # safe
    "ls": "safe", "dir": "safe", "pwd": "safe", "echo": "safe",
    "cat": "safe", "head": "safe", "tail": "safe", "less": "safe",
    "wc": "safe", "grep": "safe", "find": "safe", "which": "safe",
    "type": "safe", "where": "safe", "whoami": "safe", "hostname": "safe",
    "date": "safe", "uname": "safe", "df": "safe", "du": "safe",
    "ps": "safe", "top": "safe", "env": "safe",
    "python": "safe", "python3": "safe", "py": "safe",
    "node": "safe", "deno": "safe",
    # moderate
    "curl": "moderate", "wget": "moderate",
    "npm": "moderate", "pip": "moderate", "uv": "moderate", "pipx": "moderate",
    "git": "moderate",  # git status/diff/log 安全；push/reset/clean 下面再升级
    "make": "moderate", "cargo": "moderate", "go": "moderate",
    "docker": "moderate", "kubectl": "moderate",
    "tar": "moderate", "zip": "moderate", "unzip": "moderate",
    "cp": "moderate", "touch": "moderate", "mkdir": "moderate",
    # dangerous
    "mv": "dangerous", "rm": "dangerous", "del": "dangerous",
    "chmod": "dangerous", "chown": "dangerous",
    "kill": "dangerous", "killall": "dangerous", "pkill": "dangerous",
    "sudo": "dangerous",
    # critical
    "dd": "critical", "mkfs": "critical", "fdisk": "critical",
    "format": "critical", "shutdown": "critical", "reboot": "critical",
    "init": "critical", "halt": "critical",
}

# 整段命令字符串正则 → 提升后的危险级；命中后用高档覆盖首词档
_PATTERNS: list[tuple[re.Pattern[str], DangerLevel]] = [
    # critical
    (re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f|\brm\s+-[a-zA-Z]*f[a-zA-Z]*r"), "critical"),
    (re.compile(r"\brm\s+-rf\b"), "critical"),
    (re.compile(r":\(\)\{.*\};:"), "critical"),  # fork bomb
    (re.compile(r"\bmkfs(\.[a-z0-9]+)?\b"), "critical"),
    (re.compile(r"\bdd\s+if=.*of=/dev/"), "critical"),
    (re.compile(r"\b(shutdown|reboot|halt|poweroff)\b"), "critical"),
    (re.compile(r"\bchown\s+-R\s+.*[/~]"), "critical"),
    (re.compile(r">\s*/dev/sd[a-z]"), "critical"),
    # dangerous
    (re.compile(r"\bgit\s+push(\s+-f|\s+--force)"), "dangerous"),
    (re.compile(r"\bgit\s+reset\s+--hard"), "dangerous"),
    (re.compile(r"\bgit\s+clean\s+-[fd]"), "dangerous"),
    (re.compile(r"\bsudo\b"), "dangerous"),
    (re.compile(r"\b(curl|wget)\b.*\|\s*(sh|bash|zsh)"), "dangerous"),  # remote exec
    # moderate（git 读类降回 safe 在 _classify 内单独处理）
]

# safe 子模式：git 读类操作；命中后强制 safe
_SAFE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^git\s+(status|log|diff|show|branch|tag|remote|config\s+--get)\b"),
    re.compile(r"^npm\s+(ls|list|outdated|view|info|search)\b"),
    re.compile(r"^pip\s+(list|show|search|freeze)\b"),
    re.compile(r"^docker\s+(ps|images|logs|inspect)\b"),
]

_DANGER_ORDER: dict[DangerLevel, int] = {
    "safe": 0, "moderate": 1, "dangerous": 2, "critical": 3,
}


def _max_level(a: DangerLevel, b: DangerLevel) -> DangerLevel:
    return a if _DANGER_ORDER[a] >= _DANGER_ORDER[b] else b


def classify(command: str) -> DangerLevel:
    """对一条 shell 命令分级；空命令归 safe（dispatcher 会另行报错）。"""
    cmd = command.strip()
    if not cmd:
        return "safe"

    # safe 子模式优先：git status 这种不要被 git 通用档拖累
    for pat in _SAFE_PATTERNS:
        if pat.search(cmd):
            return "safe"

    # 首词
    first = _first_word(cmd)
    level: DangerLevel = _FIRST_WORD.get(first, "moderate")

    # 整段模式（取最大档）
    for pat, lvl in _PATTERNS:
        if pat.search(cmd):
            level = _max_level(level, lvl)

    # 复合命令（管道/分号）：在右半也分类，取最大
    for sep in (";", "&&", "||", "|"):
        if sep in cmd:
            parts = [p.strip() for p in cmd.split(sep) if p.strip()]
            for part in parts[1:]:
                level = _max_level(level, classify(part))
            break
    return level


def _first_word(cmd: str) -> str:
    """从命令字符串取首词；shlex 解析失败回退 split。"""
    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError:
        tokens = cmd.split()
    if not tokens:
        return ""
    return tokens[0].lower()


def label(level: DangerLevel) -> str:
    """中文标签；UI / 日志展示用。"""
    return {
        "safe": "安全",
        "moderate": "一般",
        "dangerous": "危险",
        "critical": "致命",
    }[level]
