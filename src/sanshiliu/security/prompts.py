"""用户确认文案 + REPL Confirmer 实现。

设计：通道层各自实现 Confirmer 协议；REPL 走 input()，wechat/web 默认 None（缺省拒绝）。
"""

from __future__ import annotations

import asyncio

from sanshiliu.foundation.logging import get_logger
from sanshiliu.security.bash_classifier import label as _danger_label
from sanshiliu.security.types import ConfirmRequest, ConfirmResponse, DecisionScope

_logger = get_logger(__name__)


def render_request(request: ConfirmRequest) -> str:
    """把 ConfirmRequest 渲染成多行文案；REPL/wechat 共用。"""
    lines = [
        "── 权限确认 ──",
        f"  工具       : {request.tool_name}  ({request.canonical_name})",
    ]
    if request.danger is not None:
        lines.append(f"  危险级别   : {_danger_label(request.danger)} ({request.danger})")
    lines.append(f"  参数预览   : {request.arguments_preview}")
    if request.matched_rule is not None:
        lines.append(f"  命中规则   : {request.matched_rule}")
    lines.append("")
    lines.append("  [a] 允许一次  [s] 本会话允许  [p] 永久允许（写 settings.json）  [d] 拒绝")
    return "\n".join(lines)


_CHOICE_MAP: dict[str, tuple[str, DecisionScope]] = {
    "a": ("allow", "once"),
    "y": ("allow", "once"),         # 兼容 yes
    "s": ("allow", "session"),
    "p": ("allow", "permanent"),
    "d": ("deny", "once"),
    "n": ("deny", "once"),          # 兼容 no
    "": ("deny", "once"),           # 空回车默认拒绝
}


def parse_choice(raw: str) -> ConfirmResponse:
    """把 'a' / 's' / 'p' / 'd' 解析成 ConfirmResponse；不识别一律视为拒绝。"""
    key = raw.strip().lower()[:1]
    decision, scope = _CHOICE_MAP.get(key, ("deny", "once"))
    return ConfirmResponse(decision=decision, scope=scope)  # type: ignore[arg-type]


class ReplConfirmer:
    """REPL 通道的 Confirmer 实现；阻塞读放线程池。"""

    async def confirm(self, request: ConfirmRequest) -> ConfirmResponse:
        text = render_request(request) + "\n请选择 > "
        raw = await asyncio.to_thread(_safe_input, text)
        resp = parse_choice(raw)
        _logger.info(
            "REPL 权限决定",
            tool=request.tool_name, decision=resp.decision, scope=resp.scope,
        )
        return resp


def _safe_input(prompt: str) -> str:
    """input() 包装；EOFError/KeyboardInterrupt 一律视作拒绝（不抛）。"""
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return "d"


class DenyAllConfirmer:
    """非交互通道的兜底 Confirmer：永远拒绝；同时记一条日志便于排查。"""

    def __init__(self, channel_name: str) -> None:
        self._channel = channel_name

    async def confirm(self, request: ConfirmRequest) -> ConfirmResponse:
        _logger.info(
            "非交互通道直接拒绝",
            channel=self._channel, tool=request.tool_name,
        )
        return ConfirmResponse(decision="deny", scope="once")
