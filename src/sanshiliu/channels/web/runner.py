"""web/wechat 联合启动器；sanshiliu serve / bot 命令的实际实现。"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys

from sanshiliu.channels.web.handlers import (
    HealthState,
    make_chat_handler,
    make_healthz_handler,
    make_metrics_handler,
    make_webhook_handler,
)
from sanshiliu.channels.web.routes import Router
from sanshiliu.channels.web.server import WebServer
from sanshiliu.channels.wechat.bot import WechatBot
from sanshiliu.channels.wechat.ilink_client import ILinkClient
from sanshiliu.channels.wechat.queue import WechatQueue
from sanshiliu.channels.wechat.rate_limit import WechatRateLimiter
from sanshiliu.channels.wechat.safety import WechatSafety
from sanshiliu.channels.wechat.webhook import WechatWebhookProcessor
from sanshiliu.channels.wechat.whitelist import WechatWhitelist
from sanshiliu.context.manager import ContextManager
from sanshiliu.context.prompts import load_compact_prompts
from sanshiliu.engine.loop import ConversationEngine
from sanshiliu.foundation.config import get_settings
from sanshiliu.foundation.errors import ConfigError
from sanshiliu.foundation.logging import configure_logging, get_logger
from sanshiliu.identity.loader import PersonaLoader
from sanshiliu.identity.watcher import PersonaWatcher
from sanshiliu.llm.client import LLMClient
from sanshiliu.memory.longterm.claudemd import ClaudeMdLoader
from sanshiliu.memory.longterm.extract import MemoryExtractor, load_extract_instruction
from sanshiliu.memory.longterm.memdir import MemdirLoader
from sanshiliu.security.path_guard import PathGuard
from sanshiliu.security.permission import PermissionManager
from sanshiliu.security.prompts import DenyAllConfirmer
from sanshiliu.security.settings_loader import SettingsLoader
from sanshiliu.skills.activator import SkillActivator
from sanshiliu.skills.loader import SkillLoader
from sanshiliu.storage.db import get_database
from sanshiliu.tools.bootstrap import build_tool_stack

_logger = get_logger(__name__)


async def run_serve() -> int:
    """sanshiliu serve：启动 HTTP server（含 /chat /healthz /metrics）+ 可选 wechat bot。"""
    try:
        settings = get_settings()
    except Exception as exc:
        print(f"配置加载失败：{exc}", file=sys.stderr)
        return 78

    configure_logging(log_level=settings.log_level, log_dir=settings.data_dir / "logs")

    # 人设 + 系统 prompts
    loader = PersonaLoader(settings.persona_dir)
    try:
        loader.load()
    except ConfigError as exc:
        print(f"人设加载失败：{exc}", file=sys.stderr)
        return 78

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
    # Phase 8：权限（web/wechat 走 DenyAllConfirmer，ask 模式直接拒绝）
    from pathlib import Path as _Path
    permission_manager: PermissionManager | None = None
    if settings.security_enabled:
        try:
            settings_loader = SettingsLoader(
                global_home=settings.home_dir, project_cwd=_Path.cwd(),
            )
            settings_loader.load()
            path_guard = PathGuard(cwd_root=_Path.cwd())
            permission_manager = PermissionManager(
                settings_loader=settings_loader,
                path_guard=path_guard,
                confirmer=DenyAllConfirmer("web/wechat"),
                db=db,
            )
        except Exception as exc:
            print(f"权限管理加载失败（继续无权限审批）：{exc}", file=sys.stderr)

    # Phase 5：tool 栈
    tool_registry = None
    tool_dispatcher = None
    if settings.tools_enabled:
        try:
            tool_registry, tool_dispatcher = build_tool_stack(
                prompts_dir=settings.prompts_dir,
                cwd_root=_Path.cwd(),
                tavily_api_key=settings.tavily_api_key.get_secret_value() if settings.tavily_api_key else None,
                permission=permission_manager,
            )
        except ConfigError as exc:
            print(f"工具栈加载失败（继续不带工具）：{exc}", file=sys.stderr)

    # Phase 6：skills
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

    # Phase 7：长期记忆（CLAUDE.md + memdir + auto-extract）
    claudemd_loader: ClaudeMdLoader | None = None
    memdir_loader: MemdirLoader | None = None
    memory_extractor: MemoryExtractor | None = None
    if settings.memory_enabled:
        from pathlib import Path as _Path
        try:
            claudemd_loader = ClaudeMdLoader(
                global_home=settings.home_dir,
                project_cwd=_Path.cwd(),
            )
            claudemd_loader.load()
            memdir_loader = MemdirLoader(settings.memdir_dir)
            memdir_loader.load()
            _logger.info(
                "memory 已加载",
                claudemd_chars=claudemd_loader.get().total_chars(),
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

    engine = ConversationEngine(
        llm=llm, db=db, persona_loader=loader, context_manager=context_manager,
        tool_registry=tool_registry, tool_dispatcher=tool_dispatcher,
        skill_activator=skill_activator,
        claudemd_loader=claudemd_loader,
        memdir_loader=memdir_loader,
        memory_extractor=memory_extractor,
    )
    persona_watcher = PersonaWatcher(loader)

    health = HealthState()
    health.set("llm", "up")
    health.set("db", "up")
    health.set("web", "up")

    loop = asyncio.get_running_loop()
    router = Router()
    router.register("POST", "/chat", make_chat_handler(engine, loop, health))
    router.register("GET", "/healthz", make_healthz_handler(db, loop, health))
    router.register("GET", "/metrics", make_metrics_handler(context_manager))

    # wechat 可选启动；webhook 路径挂在同一 web server 上
    wechat_bot: WechatBot | None = None
    if settings.wechat_enabled:
        if settings.ilink_api_key is None or settings.ilink_webhook_secret is None:
            print("wechat_enabled=true 但缺凭据；终止", file=sys.stderr)
            return 78
        client = ILinkClient(
            base_url=settings.ilink_base_url,
            api_key=settings.ilink_api_key.get_secret_value(),
        )
        queue = WechatQueue(db)
        whitelist = WechatWhitelist.from_csv(settings.wechat_whitelist)
        rate_limiter = WechatRateLimiter(
            db,
            per_user_per_day=settings.wechat_rate_per_user_per_day,
            global_per_minute=settings.wechat_rate_global_per_minute,
        )
        safety = WechatSafety(
            input_blacklist=settings.wechat_input_blacklist.split(","),
            output_blacklist=settings.wechat_output_blacklist.split(","),
        )
        wechat_bot = WechatBot(
            db=db, engine=engine, client=client, queue=queue,
            whitelist=whitelist, rate_limiter=rate_limiter, safety=safety, health=health,
        )
        processor = WechatWebhookProcessor(
            db=db,
            webhook_secret=settings.ilink_webhook_secret.get_secret_value(),
            signature_header=settings.ilink_signature_header,
        )
        router.register("POST", "/wechat/webhook", make_webhook_handler(processor.process, loop))
    else:
        health.set("wechat", "disabled")
        client = None  # type: ignore[assignment]

    server = WebServer(
        host="0.0.0.0", port=settings.web_port, router=router, loop=loop,
    )

    # 信号处理：Ctrl+C / SIGTERM 优雅退出
    stop_event = asyncio.Event()

    def _request_stop(*_: object) -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        # Windows 不支持 add_signal_handler；退化为靠 KeyboardInterrupt
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, _request_stop)

    await persona_watcher.start()
    server.start()
    if wechat_bot is not None:
        await wechat_bot.start()

    print("\n── 服务已启动 ──")
    print(f"  HTTP    : http://0.0.0.0:{settings.web_port}")
    print("    POST /chat (SSE) | GET /healthz | GET /metrics")
    if wechat_bot is not None:
        print("  Wechat  : 已启用，webhook=/wechat/webhook")
    print("  停止：Ctrl+C")

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if wechat_bot is not None:
            await wechat_bot.stop()
            await client.close()  # type: ignore[union-attr]
        server.stop()
        await persona_watcher.stop()
        await llm.close()
        await db.close()
    return 0


def run_serve_sync() -> int:
    try:
        return asyncio.run(run_serve())
    except KeyboardInterrupt:
        print("\n(已停止)")
        return 130
