"""REPL 通道；启动时装配 Persona/Context/Engine/DB；运行中支持 /quit /stats /persona /help。"""

from __future__ import annotations

import asyncio
import sys
from typing import NoReturn

from sanshiliu import __version__
from sanshiliu.context.manager import ContextManager
from sanshiliu.context.prompts import load_compact_prompts
from sanshiliu.engine.loop import ConversationEngine
from sanshiliu.engine.session import Session
from sanshiliu.foundation.config import get_settings
from sanshiliu.foundation.errors import ConfigError, LLMError, SanshiliuError
from sanshiliu.foundation.logging import configure_logging, get_logger
from sanshiliu.identity.loader import PersonaLoader
from sanshiliu.identity.watcher import PersonaWatcher
from sanshiliu.llm.client import LLMClient
from sanshiliu.skills.activator import SkillActivator
from sanshiliu.skills.loader import SkillLoader
from sanshiliu.storage.db import Database, get_database
from sanshiliu.tools.bootstrap import build_tool_stack

_logger = get_logger(__name__)

# 启动横幅模板；仅 UI 输出，不发给 LLM
_BANNER = """
╔══════════════════════════════════════════╗
║  三十六贱笑 (Sanshiliu Jianxiao) v{version:<7s}║
║  Phase 3 · 上下文管理接入                ║
╠══════════════════════════════════════════╣
║  Model   : {model:<30s}║
║  Base    : {base:<30s}║
║  Persona : {persona:<30s}║
║  Prompts : {prompts:<30s}║
╠══════════════════════════════════════════╣
║  命令: /quit /stats /persona /help       ║
╚══════════════════════════════════════════╝
"""


async def _read_line(prompt: str) -> str:
    """input 的 async 版本——阻塞读放到线程池，避免堵 event loop。"""
    return await asyncio.to_thread(input, prompt)


async def _print_stats(
    db: Database, session: Session, ctx: ContextManager | None
) -> None:
    """/stats：会话总览 + budget 详情。"""
    stats = await db.get_session_stats(session.session_id)
    print("── 本会话统计 ──")
    print(f"  调用次数  : {stats['calls']}")
    print(f"  输入 token: {stats['input_tokens']}")
    print(f"  输出 token: {stats['output_tokens']}")
    print(f"  累计成本  : ￥{stats['cost_cny']:.4f}")
    print(f"  消息条数  : {len(session.messages)}（含 system）")
    if ctx is not None:
        b = ctx.stats()
        print("── 上下文 / Budget ──")
        print(f"  最近一次 prompt_tokens : {b['last_prompt_tokens']}")
        print(f"  上下文窗口             : {b['max_tokens']}")
        print(f"  Compact 阈值           : {b['threshold']}")
        print(f"  当前利用率             : {b['utilization']:.1%}")
        print(f"  Compact 次数           : {b['compact_count']}")
        print(f"  MicroCompact 次数      : {b['microcompact_count']}")
        print(f"  Cache read / create    : {b['cache_read']} / {b['cache_create']}")
        print(f"  Compact summary 字符   : {len(session.compact_summary)}")
    print()


async def _print_persona(loader: PersonaLoader) -> None:
    snap = loader.get()
    print("── 当前人设 ──")
    print(f"  目录       : {loader.persona_dir}")
    print(f"  总字数     : {snap.total_chars()}")
    print(f"  最近 mtime : {snap.latest_mtime():.0f}")
    for name, content in snap.sections.items():
        print(f"    {name:<18} {len(content):>6} 字")
    print()


def _print_help() -> None:
    print(
        "── 命令 ──\n"
        "  /quit /exit  退出\n"
        "  /stats       会话 token / budget / compact 汇总\n"
        "  /persona     当前人设文件状态\n"
        "  /help        显示本帮助\n"
        "  其他输入     发给 agent\n"
    )


async def run_repl() -> int:
    """REPL 主循环；返回 shell 退出码。"""
    try:
        settings = get_settings()
    except Exception as exc:
        print(f"配置加载失败：{exc}", file=sys.stderr)
        return 78

    configure_logging(log_level=settings.log_level, log_dir=settings.data_dir / "logs")

    # 人设：缺文件直接拦在启动期，错误信息含友好提示
    loader = PersonaLoader(settings.persona_dir)
    try:
        loader.load()
    except ConfigError as exc:
        print(f"人设加载失败：{exc}", file=sys.stderr)
        return 78

    # Phase 3：compact prompts 也走 markdown 外置
    try:
        compact_prompts = load_compact_prompts(settings.prompts_dir)
    except ConfigError as exc:
        print(f"prompts 加载失败：{exc}", file=sys.stderr)
        return 78

    db = await get_database(settings.data_dir / "sanshiliu.db")
    llm = LLMClient(
        api_key=settings.openai_api_key.get_secret_value(),
        base_url=settings.openai_base_url,
        model=settings.openai_model,
        db=db,
    )
    context_manager = ContextManager(
        llm=llm,
        prompts=compact_prompts,
        max_context_tokens=settings.max_context_tokens,
        compact_threshold_ratio=settings.compact_threshold_ratio,
    )
    # Phase 5：tool 栈（默认开；用户可在 .env 关）
    tool_registry = None
    tool_dispatcher = None
    if settings.tools_enabled:
        try:
            from pathlib import Path as _Path
            tool_registry, tool_dispatcher = build_tool_stack(
                prompts_dir=settings.prompts_dir,
                cwd_root=_Path.cwd(),
                tavily_api_key=settings.tavily_api_key.get_secret_value() if settings.tavily_api_key else None,
            )
        except ConfigError as exc:
            print(f"工具栈加载失败（继续不带工具）：{exc}", file=sys.stderr)

    # Phase 6：skills 加载 + activator
    skill_activator: SkillActivator | None = None
    if settings.skills_enabled:
        try:
            skill_loader = SkillLoader([settings.skills_dir_project, settings.skills_dir_repo])
            skills = skill_loader.load()
            if skills:
                skill_activator = SkillActivator(skill_loader)
                _logger.info("skills 已注册", ids=[s.id for s in skills])
        except Exception as exc:
            print(f"skills 加载失败（继续不带 skills）：{exc}", file=sys.stderr)

    engine = ConversationEngine(
        llm=llm, db=db, persona_loader=loader, context_manager=context_manager,
        tool_registry=tool_registry, tool_dispatcher=tool_dispatcher,
        skill_activator=skill_activator,
    )
    session = Session.new(channel="repl")
    # 懒失效模式：watcher 仅 invalidate loader 缓存，engine 每轮拉新 snapshot；不挂 on_change
    watcher = PersonaWatcher(loader)

    print(
        _BANNER.format(
            version=__version__,
            model=settings.openai_model,
            base=settings.openai_base_url,
            persona=str(settings.persona_dir.name),
            prompts=str(settings.prompts_dir.name),
        )
    )
    _logger.info(
        "REPL 启动",
        session_id=session.session_id,
        model=settings.openai_model,
        persona_chars=loader.get().total_chars(),
        max_context_tokens=settings.max_context_tokens,
    )

    await watcher.start()
    try:
        while True:
            try:
                user_input = await _read_line("\n你> ")
            except EOFError:
                break
            user_input = user_input.strip()
            if not user_input:
                continue

            cmd = user_input.lower()
            if cmd in {"/quit", "/exit"}:
                break
            if cmd == "/stats":
                await _print_stats(db, session, context_manager)
                continue
            if cmd == "/persona":
                await _print_persona(loader)
                continue
            if cmd == "/help":
                _print_help()
                continue

            print("\n贱笑> ", end="", flush=True)
            try:
                async for delta in engine.stream_turn(session, user_input):
                    print(delta.text, end="", flush=True)
                print()
            except LLMError as exc:
                print(f"\n[LLM 错误] {exc}", file=sys.stderr)
                _logger.error("LLM 调用失败", error=str(exc), session_id=session.session_id)
            except SanshiliuError as exc:
                print(f"\n[系统错误] {exc}", file=sys.stderr)
                _logger.error("系统异常", error=str(exc), session_id=session.session_id)
    finally:
        await watcher.stop()
        await llm.close()
        await db.close()
        _logger.info("REPL 结束", session_id=session.session_id)

    return 0


def run_repl_sync() -> int:
    """sync 入口；给 cli.main / __main__.py 用。"""
    try:
        return asyncio.run(run_repl())
    except KeyboardInterrupt:
        print("\n(已退出)")
        return 130
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 78


def _unreachable() -> NoReturn:  # pragma: no cover
    raise RuntimeError("不应到达")
