"""启动横幅；含 V-3 要求的 7 项关键信息：version/model/persona/skills/memory/channels/wd。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sanshiliu import __version__


@dataclass(frozen=True)
class StatusSummary:
    """启动期采集到的各子系统状态；banner 用。"""

    version: str = __version__
    model: str = "?"
    base_url: str = "?"
    persona_dir: str = "?"
    persona_chars: int = 0
    skills_count: int = 0
    memory_chars: int = 0
    memory_entries: int = 0
    channels: tuple[str, ...] = ()
    cwd: str = "?"
    permission_mode: str = "?"
    permission_allow: int = 0
    permission_deny: int = 0


def render_status_summary(s: StatusSummary) -> str:
    """7 项关键信息纯文本（不带框线）；CLI 测试断言友好。"""
    lines = [
        f"version    : {s.version}",
        f"model      : {s.model} @ {s.base_url}",
        f"persona    : {s.persona_dir}（{s.persona_chars} 字）",
        f"skills     : {s.skills_count} 个",
        f"memory     : CLAUDE.md {s.memory_chars} 字 / memdir {s.memory_entries} 条",
        f"channels   : {', '.join(s.channels) if s.channels else '-'}",
        f"working dir: {s.cwd}",
        f"permission : {s.permission_mode}（allow={s.permission_allow} / deny={s.permission_deny}）",
    ]
    return "\n".join(lines)


def format_banner(s: StatusSummary) -> str:
    """REPL/serve 启动时打印的带边框 banner。"""
    body = render_status_summary(s)
    lines = body.splitlines()
    width = max(len(_visual_len(ln)) for ln in lines) + 4
    title = f"  三十六贱笑 v{s.version}  "
    top = "╔" + "═" * width + "╗"
    title_line = "║" + _pad(title, width) + "║"
    sep = "╠" + "═" * width + "╣"
    body_lines = ["║  " + ln + " " * (width - 2 - len(_visual_len(ln))) + "║" for ln in lines]
    bottom = "╚" + "═" * width + "╝"
    return "\n".join([top, title_line, sep, *body_lines, bottom])


def _visual_len(text: str) -> str:
    """对齐用占位：CJK 字符算 2 列，其余算 1 列。"""
    width = 0
    for ch in text:
        width += 2 if _is_wide(ch) else 1
    return " " * width


def _is_wide(ch: str) -> bool:
    """简易宽字符判定；覆盖常用 CJK 区段。"""
    code = ord(ch)
    return (
        0x1100 <= code <= 0x115F
        or 0x2E80 <= code <= 0x9FFF
        or 0xA960 <= code <= 0xA97F
        or 0xAC00 <= code <= 0xD7A3
        or 0xF900 <= code <= 0xFAFF
        or 0xFE30 <= code <= 0xFE4F
        or 0xFF00 <= code <= 0xFF60
        or 0xFFE0 <= code <= 0xFFE6
    )


def _pad(text: str, width: int) -> str:
    """居中填充到目标列宽（按 visual 列计）。"""
    actual = len(_visual_len(text))
    left = max((width - actual) // 2, 0)
    right = max(width - actual - left, 0)
    return " " * left + text + " " * right


def summary_from_paths(
    *,
    model: str,
    base_url: str,
    persona_dir: Path,
    persona_chars: int,
    skills_count: int,
    memory_chars: int,
    memory_entries: int,
    channels: tuple[str, ...],
    cwd: Path,
    permission_mode: str,
    permission_allow: int,
    permission_deny: int,
) -> StatusSummary:
    """组装 StatusSummary 的便捷函数；让调用方少打字。"""
    return StatusSummary(
        model=model, base_url=base_url,
        persona_dir=str(persona_dir.name),
        persona_chars=persona_chars,
        skills_count=skills_count,
        memory_chars=memory_chars,
        memory_entries=memory_entries,
        channels=channels,
        cwd=str(cwd),
        permission_mode=permission_mode,
        permission_allow=permission_allow,
        permission_deny=permission_deny,
    )
