"""聊天 slash 命令分发器；通道无关。

任何通道（web /chat、wechat bot、REPL）在把用户输入送进 LLM 之前，
都可以先调 try_dispatch；命令命中就用 CommandResult.reply 回给用户、不走 LLM。

新增命令：在 COMMANDS_META 注册一行（cmd 名 → (handler, 帮助文案)）。
handler 签名：async def(ctx: CommandContext, args: str) -> CommandResult
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sanshiliu.engine.types import ChatMessage
from sanshiliu.foundation.logging import get_logger
from sanshiliu.memory.longterm.consolidate import ConsolidateDiff

if TYPE_CHECKING:
    from sanshiliu.engine.loop import ConversationEngine
    from sanshiliu.engine.session import Session
    from sanshiliu.memory.shortterm import ShortTermMemory

_logger = get_logger(__name__)

# PR4：/memory consolidate 的 draft 缓存；按 session_id 暂存上次 dry-run 结果
# 5 分钟过期；不同 session 互不影响；同 session 多次 consolidate 只保留最新
_CONSOLIDATE_DRAFTS: dict[str, tuple[float, ConsolidateDiff]] = {}
_DRAFT_TTL_SEC = 300.0


@dataclass
class CommandContext:
    """命令处理上下文；通道层填充后传给 handler。"""

    session: Session
    engine: ConversationEngine
    channel: str  # "web" | "wechat" | "repl"
    # /new 用来在重置前把旧会话落盘；None 表示通道未挂短期记忆，跳过快照
    short_term: ShortTermMemory | None = None


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
    """开新会话；旧会话先快照落盘，再原地分配新 session_id 并清空所有上下文。

    保留 channel / user_id（标识会话归属，不属于"会话内容"）；
    其余字段——messages / compact_summary / active_skills_text /
    memory_block_text / active_module_text / persona_modules_listing /
    active_module_ids / last_active_module_id——全部重置为初始态。
    """
    sess = ctx.session
    old_sid = sess.session_id

    # 1) 旧会话落盘；失败不阻塞重置（jsonl 写入是 best-effort）
    if ctx.short_term is not None:
        try:
            await ctx.short_term.snapshot(sess)
        except Exception as exc:
            _logger.warning(
                "/new 旧会话 snapshot 失败（继续重置）",
                old_session_id=old_sid,
                error=str(exc),
            )

    # 2) 原地"重生"——同 Session 对象，但分配新 id、清掉一切会话内状态。
    #    原地改而不是 new Session()，是为了让各 channel 持有的局部 session 引用继续有效。
    new_sid = str(uuid.uuid4())
    sess.session_id = new_sid
    sess.created_at = time.time()
    # 占位 system 行；下一轮 engine.refresh_system_prompt 会用最新 persona 填充
    sess.messages = [ChatMessage(role="system", content="")]
    sess.compact_summary = ""
    sess.active_skills_text = ""
    sess.memory_block_text = ""
    sess.active_module_text = ""
    sess.persona_modules_listing = ""
    sess.active_module_ids.clear()
    sess.last_active_module_id = ""

    _logger.info(
        "slash /new 已新建会话",
        old_session_id=old_sid,
        new_session_id=new_sid,
        channel=ctx.channel,
    )
    return CommandResult(
        reply=(
            f"[新对话] 旧会话已保存（{old_sid[:8]}…），"
            f"现已切换到新会话 {new_sid[:8]}…"
        ),
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
    # PR1：手动压缩成功后也把 compact_summary 落 sqlite，确保 reload 不丢摘要
    try:
        await ctx.engine.persist_session_state(sess)
    except Exception as exc:
        _logger.warning("/compact 后持久化 session state 失败（不阻塞）", error=str(exc))
    return CommandResult(
        reply=f"[压缩] 消息 {before} -> {after} 条，"
              f"摘要 {len(sess.compact_summary)} 字。",
    )


def _format_diff_reply(diff: ConsolidateDiff) -> str:
    """把 ConsolidateDiff 渲染成对人友好的多段文本；用于 /memory consolidate dry-run 回复。"""
    if diff.is_empty:
        return "[consolidate] memdir 没有需要合并/删除/重写的条目。"
    parts: list[str] = [
        f"[consolidate dry-run] 共 {diff.total_ops} 项变更：",
        "",
    ]
    if diff.merge:
        parts.append(f"## 合并 {len(diff.merge)} 组")
        for m in diff.merge:
            drop_str = ", ".join(m.drop)
            preview = m.new_body.replace("\n", " ")[:60]
            parts.append(f"  - keep [{m.keep}]，drop [{drop_str}]")
            parts.append(f"    new_body: {preview}...")
        parts.append("")
    if diff.delete:
        parts.append(f"## 删除 {len(diff.delete)} 条")
        for d in diff.delete:
            parts.append(f"  - {d.name}：{d.reason}")
        parts.append("")
    if diff.rewrite:
        parts.append(f"## 重写 {len(diff.rewrite)} 条")
        for r in diff.rewrite:
            preview = r.new_body.replace("\n", " ")[:60]
            parts.append(f"  - {r.name}：{preview}...")
        parts.append("")
    if diff.warnings:
        parts.append("## 警告")
        for w in diff.warnings:
            parts.append(f"  - {w}")
        parts.append("")
    parts.append("输入 /memory apply 落盘 / /memory cancel 取消（5 分钟内有效）")
    return "\n".join(parts)


async def cmd_memory(ctx: CommandContext, args: str) -> CommandResult:
    """记忆维护：consolidate (dry-run) / apply (落盘) / cancel / help。"""
    sub, _, _rest = args.strip().partition(" ")
    sub = sub.lower().strip() or "help"

    if sub == "help":
        lines = [
            "── /memory 子命令 ──",
            "  /memory consolidate  扫描 memdir，给出合并/删除/重写 diff（不落盘）",
            "  /memory apply        落盘上次 dry-run 的 diff",
            "  /memory cancel       取消未应用的 dry-run",
            "  /memory help         本帮助",
        ]
        return CommandResult(reply="\n".join(lines))

    sid = ctx.session.session_id

    if sub == "cancel":
        _CONSOLIDATE_DRAFTS.pop(sid, None)
        return CommandResult(reply="[consolidate] 已取消未应用的 dry-run（如有）。")

    if sub == "consolidate":
        consolidator = ctx.engine.get_memory_consolidator()
        if consolidator is None:
            return CommandResult(
                reply=(
                    "[consolidate] 不可用：缺少 memdir_loader 或 prompts/memory_consolidate.md。"
                    "检查 settings.memory_enabled 与 prompts 目录。"
                ),
            )
        try:
            diff = await consolidator.dry_run(session_id=sid)
        except Exception as exc:
            _logger.exception("/memory consolidate 失败", error=str(exc))
            return CommandResult(reply=f"[consolidate] 失败：{type(exc).__name__}: {exc}")
        if diff.is_empty:
            return CommandResult(reply=_format_diff_reply(diff))
        _CONSOLIDATE_DRAFTS[sid] = (time.time(), diff)
        return CommandResult(reply=_format_diff_reply(diff))

    if sub == "apply":
        entry = _CONSOLIDATE_DRAFTS.get(sid)
        if entry is None:
            return CommandResult(
                reply="[consolidate] 无可应用的 diff；请先跑 /memory consolidate。",
            )
        created_at, diff = entry
        if time.time() - created_at > _DRAFT_TTL_SEC:
            _CONSOLIDATE_DRAFTS.pop(sid, None)
            return CommandResult(
                reply=(
                    "[consolidate] 上次 dry-run 已过期（>5 分钟），"
                    "请重新跑 /memory consolidate。"
                ),
            )
        consolidator = ctx.engine.get_memory_consolidator()
        if consolidator is None:
            _CONSOLIDATE_DRAFTS.pop(sid, None)
            return CommandResult(reply="[consolidate] 不可用，无法落盘。")
        try:
            res = await consolidator.apply(diff)
        except Exception as exc:
            _logger.exception("/memory apply 失败", error=str(exc))
            return CommandResult(reply=f"[consolidate apply] 失败：{type(exc).__name__}: {exc}")
        _CONSOLIDATE_DRAFTS.pop(sid, None)
        lines = [
            "[consolidate apply] 完成：",
            (
                f"  合并 {res.merged_count} 组 / 删除 {res.deleted_count} 条 / "
                f"重写 {res.rewritten_count} 条"
            ),
        ]
        if res.errors:
            lines.append(f"  错误 {len(res.errors)} 条：")
            for err in res.errors:
                lines.append(f"    - {err}")
        return CommandResult(reply="\n".join(lines))

    return CommandResult(
        reply=f"未知子命令：/memory {sub}。输入 /memory help 查看子命令。",
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
    "new":     (cmd_new,     "开新对话；旧会话快照保存，分配新 session_id"),
    "compact": (cmd_compact, "立即压缩上下文为摘要"),
    "memory":  (cmd_memory,  "记忆维护：/memory consolidate | apply | cancel | help"),
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
