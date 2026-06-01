"""REPL 通道；启动时装配 Persona/Context/Engine/DB；运行中支持 /quit /stats /persona /help。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import NoReturn

from sanshiliu import __version__
from sanshiliu.context.manager import ContextManager
from sanshiliu.context.prompts import load_compact_prompts
from sanshiliu.engine.commands import COMMANDS_META, CommandContext, try_dispatch
from sanshiliu.engine.loop import ConversationEngine
from sanshiliu.engine.session import Session
from sanshiliu.foundation.config import get_settings
from sanshiliu.foundation.errors import ConfigError, LLMError, SanshiliuError
from sanshiliu.foundation.logging import configure_logging, get_logger
from sanshiliu.foundation.msg_split import StreamingSplitter
from sanshiliu.identity.loader import PersonaLoader
from sanshiliu.identity.module_activator import PersonaModuleActivator
from sanshiliu.identity.module_loader import PersonaModuleLoader
from sanshiliu.identity.watcher import PersonaWatcher
from sanshiliu.llm.providers import build_default_registry
from sanshiliu.llm.router import LLMRouter
from sanshiliu.memory.longterm.claudemd import ClaudeMdLoader
from sanshiliu.memory.longterm.consolidate import load_consolidate_instruction
from sanshiliu.memory.longterm.extract import MemoryExtractor, load_extract_instruction
from sanshiliu.memory.longterm.memdir import MemdirLoader
from sanshiliu.memory.shortterm import ShortTermMemory
from sanshiliu.scheduler import make_active_core_provider
from sanshiliu.security.path_guard import PathGuard
from sanshiliu.security.permission import PermissionManager
from sanshiliu.security.prompts import ReplConfirmer
from sanshiliu.security.settings_loader import SettingsLoader
from sanshiliu.skills.activator import SkillActivator
from sanshiliu.skills.loader import SkillLoader
from sanshiliu.storage.db import Database, get_database
from sanshiliu.tools.bootstrap import build_tool_stack

_logger = get_logger(__name__)

# 启动横幅模板；仅 UI 输出，不发给 LLM
_BANNER = """
╔══════════════════════════════════════════╗
║  三十六贱笑 (Sanshiliu Jianxiao) v{version:<7s}║
║  Phase 7 · 长期记忆接入                  ║
╠══════════════════════════════════════════╣
║  Model   : {model:<30s}║
║  Base    : {base:<30s}║
║  Persona : {persona:<30s}║
║  Prompts : {prompts:<30s}║
║  Memory  : {memory:<30s}║
╠══════════════════════════════════════════╣
║  命令: /quit /stats /persona /memory /help ║
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


async def _print_persona(
    loader: PersonaLoader,
    module_loader: PersonaModuleLoader | None = None,
) -> None:
    snap = loader.get()
    print("── 当前人设（core，全量常驻）──")
    print(f"  根目录     : {loader.persona_dir}")
    print(f"  core 总字数: {snap.total_chars()}")
    print(f"  最近 mtime : {snap.latest_mtime():.0f}")
    for name in snap.file_order:
        print(f"    {name:<22} {len(snap.sections.get(name, '')):>6} 字")
    if module_loader is not None:
        mods = module_loader.list()
        print()
        print(f"── persona modules（按需加载，共 {len(mods)} 个）──")
        for m in mods:
            kws = "/".join(m.trigger_keywords[:5]) if m.trigger_keywords else "(无关键词)"
            print(f"  - {m.id:<22} {len(m.body):>5} 字  ← {kws}")
    print()


async def _print_stats_persona(session: Session) -> None:
    """/stats 末尾输出本会话上一轮命中的 persona module。"""
    last = session.last_active_module_id or "(none)"
    print(f"  上一轮命中 module       : {last}")


def _print_memory(
    claudemd_loader: ClaudeMdLoader | None,
    memdir_loader: MemdirLoader | None,
    extractor: MemoryExtractor | None,
) -> None:
    """/memory：长期记忆状态总览。"""
    print("── 长期记忆（CLAUDE.md + memdir）──")
    if claudemd_loader is None:
        print("  CLAUDE.md  : (memory_enabled=false，未加载)")
    else:
        snap = claudemd_loader.get()
        print(f"  全局 CLAUDE.md : {snap.global_path} ({len(snap.global_text)} 字)")
        print(f"  项目 CLAUDE.md : {snap.project_path} ({len(snap.project_text)} 字)")
    if memdir_loader is None:
        print("  memdir     : (未加载)")
    else:
        mem = memdir_loader.get()
        by = mem.by_type()
        print(f"  memdir 根    : {memdir_loader.root}")
        print(
            "  分类条数   : "
            f"user={len(by['user'])} feedback={len(by['feedback'])} "
            f"project={len(by['project'])} reference={len(by['reference'])}"
        )
        idx_lines = [ln for ln in mem.index_text.splitlines() if ln.strip().startswith("-")]
        print(f"  MEMORY.md  : {len(idx_lines)} 行索引")
    print(f"  auto-extract: {'on' if extractor is not None else 'off'}")
    print()


def _print_help() -> None:
    lines = [
        "── 命令 ──",
        "  /quit /exit  退出",
        "  /stats       会话 token / budget / compact 汇总",
        "  /persona     当前人设文件状态",
        "  /memory      长期记忆（CLAUDE.md + memdir）状态",
    ]
    # 共享 slash 命令（/new /compact /help 等），从注册表自动拉取
    for name in sorted(COMMANDS_META):
        _, doc = COMMANDS_META[name]
        lines.append(f"  /{name:<10s} {doc}")
    lines.append("  其它输入     发给 agent")
    print("\n".join(lines) + "\n")


async def run_repl() -> int:
    """REPL 主循环；返回 shell 退出码。"""
    try:
        settings = get_settings()
    except Exception as exc:
        print(f"配置加载失败：{exc}", file=sys.stderr)
        return 78

    configure_logging(log_level=settings.log_level, log_dir=settings.data_dir / "logs")

    # 成长人格覆盖（PR2）：REPL 不跑 scheduler（不会推进成长），但要让日常对话以"已长成的人格"
    # 回应——provider 读 growth-state.json 的 active_persona_chapter 解析到 chapter-N 覆盖目录，
    # 无成长则回落 base core。watcher 5s 轮询激活目录 mtime，serve 进程改了人格这边也能跟上。
    active_core_provider = make_active_core_provider(
        settings.data_dir / "growth-state.json", settings.data_dir
    )

    # 人设：缺文件直接拦在启动期，错误信息含友好提示
    loader = PersonaLoader(settings.persona_dir, active_core_provider=active_core_provider)
    try:
        loader.load()
    except ConfigError as exc:
        print(f"人设加载失败：{exc}", file=sys.stderr)
        return 78

    # persona modules（可选；目录不存在/为空返空列表，不报错）
    module_loader = PersonaModuleLoader(settings.persona_dir)
    module_loader.load()
    module_activator = (
        PersonaModuleActivator(module_loader) if module_loader.list() else None
    )

    # Phase 3：compact prompts 也走 markdown 外置
    try:
        compact_prompts = load_compact_prompts(settings.prompts_dir)
    except ConfigError as exc:
        print(f"prompts 加载失败：{exc}", file=sys.stderr)
        return 78

    db = await get_database(settings.data_dir / "sanshiliu.db")
    # 同 web/wire：走多后端 router，让带 image_url 的请求按 capability 路由到豆包。
    llm = LLMRouter(build_default_registry(settings, db=db))
    context_manager = ContextManager(
        llm=llm,
        prompts=compact_prompts,
        max_context_tokens=settings.max_context_tokens,
        compact_threshold_ratio=settings.compact_threshold_ratio,
    )
    # Phase 8：权限管理（settings.json + 状态机 + REPL Confirmer）
    permission_manager: PermissionManager | None = None
    if settings.security_enabled:
        try:
            settings_loader = SettingsLoader(
                global_home=settings.home_dir, project_cwd=Path.cwd(),
            )
            settings_loader.load()
            path_guard = PathGuard(cwd_root=Path.cwd())
            permission_manager = PermissionManager(
                settings_loader=settings_loader,
                path_guard=path_guard,
                confirmer=ReplConfirmer(),
                db=db,
            )
            _logger.info(
                "权限管理已启用",
                default_mode=settings_loader.get().default_mode,
                allow_count=len(settings_loader.get().allow),
                deny_count=len(settings_loader.get().deny),
            )
        except Exception as exc:
            print(f"权限管理加载失败（继续无权限审批）：{exc}", file=sys.stderr)

    # Phase 6：skills 加载 + activator（先于工具栈构造；工具栈用 activator 暴露 Skill 工具）
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

    # Phase 7：长期记忆（先于 L7 工具栈构造；LoadMemory/SaveMemory 工具需要 memdir_loader）
    claudemd_loader: ClaudeMdLoader | None = None
    memdir_loader: MemdirLoader | None = None
    memory_extractor: MemoryExtractor | None = None
    short_term: ShortTermMemory | None = None
    consolidate_instruction: str | None = None
    if settings.memory_enabled:
        try:
            claudemd_loader = None  # 不再加载 CLAUDE.md，只用 memdir
            memdir_loader = MemdirLoader(settings.memdir_dir)
            memdir_loader.load()
            short_term = ShortTermMemory(settings.data_dir)
            _logger.info(
                "memory 已加载",
                memdir_entries=len(memdir_loader.get().entries),
            )
        except Exception as exc:
            print(f"memory 加载失败（继续不带 memory）：{exc}", file=sys.stderr)
            claudemd_loader = None
            memdir_loader = None

        if settings.auto_extract_enabled and memdir_loader is not None:
            try:
                instruction = load_extract_instruction(settings.prompts_dir)
                memory_extractor = MemoryExtractor(
                    llm=llm, memdir_root=memdir_loader.root, instruction=instruction,
                )
                _logger.info("auto-extract 已开启")
            except ConfigError as exc:
                print(f"auto-extract 加载失败（继续不带 extract）：{exc}", file=sys.stderr)

        # PR4：/memory consolidate 指令；缺失就 None，命令运行时给提示
        if memdir_loader is not None:
            try:
                consolidate_instruction = load_consolidate_instruction(settings.prompts_dir)
            except ConfigError as exc:
                print(
                    f"memory_consolidate 指令加载失败（/memory consolidate 不可用）：{exc}",
                    file=sys.stderr,
                )

    # Phase 5：tool 栈（默认开；用户可在 .env 关）
    # PR2：memdir_loader 已构造好，build_tool_stack 会注册 LoadMemory / SaveMemory
    tool_registry = None
    tool_dispatcher = None
    if settings.tools_enabled:
        try:
            tool_registry, tool_dispatcher = build_tool_stack(
                prompts_dir=settings.prompts_dir,
                cwd_root=Path.cwd(),
                tavily_api_key=settings.tavily_api_key.get_secret_value() if settings.tavily_api_key else None,
                permission=permission_manager,
                skill_activator=skill_activator,
                persona_module_activator=module_activator,
                memdir_loader=memdir_loader,
                short_term=short_term,
                db=db,
            )
        except ConfigError as exc:
            print(f"工具栈加载失败（继续不带工具）：{exc}", file=sys.stderr)

    engine = ConversationEngine(
        llm=llm, db=db, persona_loader=loader, context_manager=context_manager,
        tool_registry=tool_registry, tool_dispatcher=tool_dispatcher,
        skill_activator=skill_activator,
        claudemd_loader=claudemd_loader,
        memdir_loader=memdir_loader,
        memory_extractor=memory_extractor,
        persona_module_activator=module_activator,
        short_term=short_term,
        consolidate_instruction=consolidate_instruction,
    )
    # PR1：尝试 reload 最近一次 repl 会话；失败/无记录 → 新建
    session: Session | None = None
    reloaded_msg_count = 0
    if short_term is not None:
        try:
            recent_sid = await db.find_recent_session_id(channel="repl", user_id=None)
        except Exception as exc:
            _logger.warning("查找最近 repl 会话失败（走新建）", error=str(exc))
            recent_sid = None
        if recent_sid:
            try:
                reloaded = await short_term.reload(recent_sid)
            except Exception as exc:
                _logger.warning("reload jsonl 失败（走新建）", error=str(exc))
                reloaded = []
            try:
                row = await db.get_session(recent_sid)
            except Exception as exc:
                _logger.warning("读取 session 元信息失败（走新建）", error=str(exc))
                row = None
            session = Session(session_id=recent_sid, channel="repl")
            if reloaded:
                session.messages.extend(reloaded)
                reloaded_msg_count = len(reloaded)
            if row:
                session.compact_summary = str(row.get("compact_summary") or "")
                ids_raw = str(row.get("active_module_ids") or "")
                session.active_module_ids = {
                    s for s in (x.strip() for x in ids_raw.split(",")) if s
                }
    if session is None:
        session = Session.new(channel="repl")
    # 懒失效模式：watcher 同时监控 core/ 和 modules/
    watcher = PersonaWatcher(loader, module_loader=module_loader)

    if claudemd_loader is not None and memdir_loader is not None:
        mem_summary = (
            f"CLAUDE.md {claudemd_loader.get().total_chars()}字 / "
            f"memdir {len(memdir_loader.get().entries)}条"
        )
    elif claudemd_loader is not None:
        mem_summary = f"CLAUDE.md {claudemd_loader.get().total_chars()}字 / memdir-off"
    else:
        mem_summary = "off"
    print(
        _BANNER.format(
            version=__version__,
            model=settings.openai_model,
            base=settings.openai_base_url,
            persona=str(settings.persona_dir.name),
            prompts=str(settings.prompts_dir.name),
            memory=mem_summary,
        )
    )
    # PR1：banner 后再补一行 session 状态
    if reloaded_msg_count > 0:
        print(f"  已加载上次会话 {session.session_id[:8]}... ({reloaded_msg_count} 条消息)\n")
    else:
        print(f"  新会话 {session.session_id[:8]}...\n")
    _logger.info(
        "REPL 启动",
        session_id=session.session_id,
        model=settings.openai_model,
        persona_chars=loader.get().total_chars(),
        max_context_tokens=settings.max_context_tokens,
        reloaded_msgs=reloaded_msg_count,
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
                await _print_stats_persona(session)
                print()
                continue
            if cmd == "/persona":
                await _print_persona(loader, module_loader)
                continue
            if cmd == "/memory":
                _print_memory(claudemd_loader, memdir_loader, memory_extractor)
                continue
            if cmd == "/help":
                _print_help()
                continue

            # 共享 slash 命令（/new /compact ...）；命中就 print reply 不走 LLM
            if user_input.startswith("/"):
                cmd_ctx = CommandContext(
                    session=session, engine=engine, channel="repl",
                    short_term=short_term,
                )
                result = await try_dispatch(user_input, cmd_ctx)
                if result is not None:
                    print(result.reply)
                    # /new 已在 handler 内对旧会话做过快照；这里再写一次记录的是"新会话初始态"，
                    # 用于 dashboard 历史能反映重置结果。失败不阻塞。
                    if short_term is not None:
                        try:
                            await short_term.snapshot(session)
                        except Exception as exc:
                            _logger.warning("REPL 命令后 snapshot 失败", error=str(exc))
                    continue

            print("\n贱笑> ", end="", flush=True)
            sp = StreamingSplitter()
            first = True
            try:
                async for delta in engine.stream_turn(session, user_input):
                    for seg in sp.feed(delta.text):
                        if not first:
                            print("\n贱笑> ", end="", flush=True)
                        print(seg, end="", flush=True)
                        first = False
                for seg in sp.close():
                    if not first:
                        print("\n贱笑> ", end="", flush=True)
                    print(seg, end="", flush=True)
                    first = False
                print()
            except LLMError as exc:
                print(f"\n[LLM 错误] {exc}", file=sys.stderr)
                _logger.error("LLM 调用失败", error=str(exc), session_id=session.session_id)
            except SanshiliuError as exc:
                print(f"\n[系统错误] {exc}", file=sys.stderr)
                _logger.error("系统异常", error=str(exc), session_id=session.session_id)
    finally:
        await watcher.stop()
        if short_term is not None:
            await short_term.snapshot(session)
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
