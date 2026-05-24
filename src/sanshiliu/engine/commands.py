"""聊天 slash 命令分发器；通道无关。

任何通道（web /chat、wechat bot、REPL）在把用户输入送进 LLM 之前，
都可以先调 try_dispatch；命令命中就用 CommandResult.reply 回给用户、不走 LLM。

新增命令：在 COMMANDS_META 注册一行（cmd 名 → (handler, 帮助文案)）。
handler 签名：async def(ctx: CommandContext, args: str) -> CommandResult
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sanshiliu.foundation.logging import get_logger

if TYPE_CHECKING:
    from sanshiliu.engine.loop import ConversationEngine
    from sanshiliu.engine.session import Session

_logger = get_logger(__name__)


@dataclass
class CommandContext:
    """命令处理上下文；通道层填充后传给 handler。"""

    session: Session
    engine: ConversationEngine
    channel: str  # "web" | "wechat" | "repl"


@dataclass
class CommandResult:
    """命令结果；reply 是回给用户的文本，session_reset 标记上下文已清空。"""

    reply: str
    session_reset: bool = False


CommandHandler = Callable[[CommandContext, str], Awaitable[CommandResult]]


def is_slash_command(text: str) -> bool:
    """是否看起来像 slash 命令；不做命名校验。"""
    s = text.strip()
    return len(s) > 1 and s.startswith("/")


async def try_dispatch(text: str, ctx: CommandContext) -> CommandResult | None:
    """命中已注册命令则返 CommandResult；未命中 / 文本不是命令时返 None。

    返 None 时，通道层应当照常把 text 送进 engine。
    返 CommandResult 时（包括未知命令的 "未知命令" 提示），通道层直接回 reply 给用户、不走 LLM。
    """
    s = text.strip()
    if not is_slash_command(s):
        return None
    # 把 "/foo bar baz" 拆成 cmd="foo", args="bar baz"
    body = s[1:]
    head, _, rest = body.partition(" ")
    cmd = head.lower().strip()
    args = rest.strip()
    handler_entry = COMMANDS_META.get(cmd)
    if handler_entry is None:
        # 未知命令：给一个友好提示，不要默默把 "/xxx" 当 LLM 输入
        names = ", ".join(f"/{k}" for k in sorted(COMMANDS_META))
        return CommandResult(reply=f"未知命令：/{cmd}。可用：{names}（输入 /help 看说明）")
    handler, _doc = handler_entry
    try:
        return await handler(ctx, args)
    except Exception as exc:
        _logger.exception("slash 命令处理失败", cmd=cmd, error=str(exc))
        return CommandResult(reply=f"命令 /{cmd} 执行失败：{type(exc).__name__}: {exc}")


# ────────── 命令实现 ──────────

async def cmd_new(ctx: CommandContext, args: str) -> CommandResult:
    """清空当前会话上下文；保留 system 消息和 session_id。"""
    sess = ctx.session
    # 保留 [0] system；其它一律清掉
    if sess.messages and sess.messages[0].role == "system":
        sess.messages = [sess.messages[0]]
    else:
        sess.messages = []
    sess.compact_summary = ""
    sess.active_skills_text = ""
    # memory_block_text 是每轮 engine 自己刷新的，不动
    _logger.info("slash /new 已清空会话", session_id=sess.session_id, channel=ctx.channel)
    return CommandResult(
        reply="[新对话] 已清空当前会话上下文。",
        session_reset=True,
    )


async def cmd_compact(ctx: CommandContext, args: str) -> CommandResult:
    """强制压缩当前会话上下文为摘要，无视 budget 阈值。"""
    cm = ctx.engine.context_manager
    if cm is None:
        return CommandResult(reply="未启用上下文管理器，无法压缩。")
    sess = ctx.session
    before = len(sess.messages)
    try:
        # 直接调内部 compactor，跳过 should_compact 阈值检查
        ok = await cm._compactor.compact(sess)  # type: ignore[attr-defined]
    except Exception as exc:
        _logger.exception("/compact 失败", error=str(exc))
        return CommandResult(reply=f"压缩失败：{type(exc).__name__}: {exc}")
    after = len(sess.messages)
    if not ok:
        return CommandResult(reply="[压缩] 上下文消息太少或失败，本次未压缩。")
    return CommandResult(
        reply=f"[压缩] 消息 {before} -> {after} 条，"
              f"摘要 {len(sess.compact_summary)} 字。",
    )


async def cmd_help(ctx: CommandContext, args: str) -> CommandResult:
    lines = ["── 可用命令 ──"]
    for name in sorted(COMMANDS_META):
        _, doc = COMMANDS_META[name]
        lines.append(f"  /{name:<10s} {doc}")
    lines.append("  其它输入会发给 agent。")
    return CommandResult(reply="\n".join(lines))


# ────────── 注册表 ──────────
# 新加命令：append 一行即可
COMMANDS_META: dict[str, tuple[CommandHandler, str]] = {
    "new":     (cmd_new,     "开新对话，清空当前会话上下文"),
    "compact": (cmd_compact, "立即压缩上下文为摘要"),
    "help":    (cmd_help,    "列出可用命令"),
}


__all__ = [
    "CommandContext",
    "CommandResult",
    "CommandHandler",
    "is_slash_command",
    "try_dispatch",
    "COMMANDS_META",
]
