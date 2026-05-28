"""web/wechat 联合启动器；sanshiliu serve / bot 命令的实际实现。"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys

from sanshiliu.channels.web.api import (
    make_channels_handler,
    make_health_api_handler,
    make_memory_file_handler,
    make_memory_handler,
    make_overview_handler,
    make_permissions_handler,
    make_persona_file_handler,
    make_persona_handler,
    make_session_messages_handler,
    make_sessions_handler,
    make_settings_json_handler,
    make_skill_structure_handler,
    make_skills_handler,
    make_tool_calls_handler,
    make_tools_handler,
)
from sanshiliu.channels.web.api_heartbeat import (
    make_heartbeat_dispatch_handler,
    make_heartbeat_list_handler,
)
from sanshiliu.channels.web.api_settings import (
    make_get_settings_handler,
    make_put_settings_handler,
)
from sanshiliu.channels.web.api_wechat import (
    WechatQrBroker,
    make_wechat_qr_cancel_handler,
    make_wechat_qr_start_handler,
    make_wechat_qr_status_handler,
)
from sanshiliu.channels.web.api_writes import (
    make_instance_reload_handler,
    make_memory_create_handler,
    make_memory_modify_handler,
    make_permissions_default_mode_handler,
    make_permissions_rule_handler,
    make_persona_write_handler,
    make_session_delete_handler,
    make_session_new_handler,
    make_settings_json_write_handler,
    make_skills_reload_handler,
)
from sanshiliu.channels.web.approvals import (
    WebApprovalBroker,
    WebApprovalConfirmer,
    make_tool_approval_handler,
)
from sanshiliu.channels.web.auth import (
    DashboardAuth,
    make_auth_login_handler,
    make_auth_logout_handler,
    make_auth_status_handler,
)
from sanshiliu.channels.web.handlers import (
    HealthState,
    SessionStore,
    make_chat_handler,
    make_healthz_handler,
    make_metrics_handler,
    make_webhook_handler,
)
from sanshiliu.channels.web.routes import Router
from sanshiliu.channels.web.server import WebServer
from sanshiliu.channels.web.static import (
    make_dashboard_handler,
    make_root_redirect_handler,
)
from sanshiliu.channels.wechat.approvals import (
    WechatApprovalBroker,
    WechatApprovalConfirmer,
)
from sanshiliu.channels.wechat.bot import WechatBot
from sanshiliu.channels.wechat.ilink_client import ILinkClient
from sanshiliu.channels.wechat.ilink_poller import ILinkLongPoller
from sanshiliu.channels.wechat.queue import WechatQueue
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
from sanshiliu.scheduler import (
    HeartbeatScheduler,
    apply_state_to_scheduler,
    build_dream_task,
    heartbeat_state_path,
    load_heartbeat_state,
    save_heartbeat_state,
)
from sanshiliu.security.composite_confirmer import CompositeConfirmer
from sanshiliu.security.path_guard import PathGuard
from sanshiliu.security.permission import PermissionManager
from sanshiliu.security.settings_loader import SettingsLoader
from sanshiliu.skills.activator import SkillActivator
from sanshiliu.skills.loader import SkillLoader
from sanshiliu.skills.structure import skill_structure_path
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

    # persona modules（可选）
    module_loader = PersonaModuleLoader(settings.persona_dir)
    module_loader.load()
    module_activator = (
        PersonaModuleActivator(module_loader) if module_loader.list() else None
    )

    try:
        compact_prompts = load_compact_prompts(settings.prompts_dir)
    except ConfigError as exc:
        print(f"prompts 加载失败：{exc}", file=sys.stderr)
        return 78

    db = await get_database(settings.data_dir / "sanshiliu.db")
    # 走多后端 router：image 请求按 vision capability 路由到豆包，
    # 否则全部沿用 default（openai_*）；与 wire/repl 共用同一份注册逻辑。
    llm = LLMRouter(build_default_registry(settings, db=db))
    context_manager = ContextManager(
        llm=llm,
        prompts=compact_prompts,
        max_context_tokens=settings.max_context_tokens,
        compact_threshold_ratio=settings.compact_threshold_ratio,
    )
    approval_broker = WebApprovalBroker()
    wechat_approval_broker = WechatApprovalBroker()

    # Phase 8：权限（web chat 通过 SSE + POST 做交互式工具审批；
    # wechat 通过用户回复 /confirm /always /refuse；CompositeConfirmer 根据 contextvar 路由）
    from pathlib import Path as _Path

    permission_manager: PermissionManager | None = None
    settings_loader: SettingsLoader | None = None
    if settings.security_enabled:
        try:
            settings_loader = SettingsLoader(
                global_home=settings.home_dir,
                project_cwd=_Path.cwd(),
            )
            settings_loader.load()
            path_guard = PathGuard(cwd_root=_Path.cwd())
            permission_manager = PermissionManager(
                settings_loader=settings_loader,
                path_guard=path_guard,
                confirmer=CompositeConfirmer(
                    web=WebApprovalConfirmer(approval_broker),
                    wechat=WechatApprovalConfirmer(wechat_approval_broker),
                ),
                db=db,
            )
        except Exception as exc:
            print(f"权限管理加载失败（继续无权限审批）：{exc}", file=sys.stderr)
            settings_loader = None

    # Phase 6：skills（先于工具栈构造；工具栈把 Skill 暴露给 LLM 时需要 activator）
    skill_activator: SkillActivator | None = None
    skill_loader: SkillLoader | None = None
    if settings.skills_enabled:
        try:
            skill_loader = SkillLoader([settings.skills_dir_project, settings.skills_dir_repo])
            skills = skill_loader.load()
            if skills:
                skill_activator = SkillActivator(skill_loader)
                _logger.info(
                    "skills 已注册",
                    ids=[s.id for s in skills],
                    structures={s.id: str(skill_structure_path(s)) for s in skills},
                )
        except Exception as exc:
            print(f"skills 加载失败（继续不带 skills）：{exc}", file=sys.stderr)
            skill_loader = None

    # Phase 7：长期记忆（先于 L7 工具栈构造；LoadMemory/SaveMemory 工具需要 memdir_loader）
    claudemd_loader: ClaudeMdLoader | None = None
    memdir_loader: MemdirLoader | None = None
    memory_extractor: MemoryExtractor | None = None
    consolidate_instruction: str | None = None
    if settings.memory_enabled:
        try:
            memdir_loader = MemdirLoader(settings.memdir_dir)
            memdir_loader.load()
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
                    llm=llm,
                    memdir_root=memdir_loader.root,
                    instruction=instruction,
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

    # 与 wire.py / repl 统一到 data_dir/shortterm 子目录下；
    # 提前构造（build_tool_stack 需要它注入 LoadMemory 的 session 查询路径）
    short_term = ShortTermMemory(settings.data_dir)

    # Phase 5：tool 栈
    # PR2：memdir_loader 已构造好，build_tool_stack 会注册 LoadMemory / SaveMemory
    tool_registry = None
    tool_dispatcher = None
    if settings.tools_enabled:
        try:
            tool_registry, tool_dispatcher = build_tool_stack(
                prompts_dir=settings.prompts_dir,
                cwd_root=_Path.cwd(),
                tavily_api_key=settings.tavily_api_key.get_secret_value()
                if settings.tavily_api_key
                else None,
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
        llm=llm,
        db=db,
        persona_loader=loader,
        context_manager=context_manager,
        tool_registry=tool_registry,
        tool_dispatcher=tool_dispatcher,
        skill_activator=skill_activator,
        claudemd_loader=claudemd_loader,
        memdir_loader=memdir_loader,
        memory_extractor=memory_extractor,
        persona_module_activator=module_activator,
        short_term=short_term,
        consolidate_instruction=consolidate_instruction,
    )
    persona_watcher = PersonaWatcher(loader, module_loader=module_loader)

    # 心跳调度（heartbeat）：通用周期任务框架；当前注册一个 dream task
    # serve 进程长跑才有意义；dashboard 可手动 enable/disable + run-now + 改配置
    # 状态持久化到 <data_dir>/heartbeat.json：env 是首次启动 seed，JSON 是 runtime 真相源
    heartbeat = HeartbeatScheduler()
    heartbeat.register(
        build_dream_task(
            engine=engine,
            db=db,
            sessions_dir=settings.data_dir / "sessions",
            memdir_dir=settings.memdir_dir,
            fire_hour=settings.dream_scheduler_hour,
            min_sessions=settings.dream_scheduler_min_sessions,
            enabled=settings.dream_scheduler_enabled,
        )
    )
    _hb_state_path = heartbeat_state_path(settings.data_dir)
    apply_state_to_scheduler(heartbeat, load_heartbeat_state(_hb_state_path))
    heartbeat.set_change_hook(lambda: save_heartbeat_state(_hb_state_path, heartbeat))

    health = HealthState()
    health.set("llm", "up")
    health.set("db", "up")
    health.set("web", "up")

    loop = asyncio.get_running_loop()
    router = Router()
    # PR1：SessionStore 注入 short_term + db，让进程重启后能从 jsonl + sqlite reload
    session_store = SessionStore(short_term=short_term, db=db)
    router.register("POST", "/chat", make_chat_handler(
        engine, loop, health, session_store, short_term, approval_broker,
        multimodal_max_images=settings.multimodal_max_images_per_turn,
        multimodal_max_image_bytes=settings.multimodal_max_image_bytes,
    ))
    router.register("GET", "/healthz", make_healthz_handler(db, loop, health))
    router.register("GET", "/metrics", make_metrics_handler(context_manager))
    dashboard_auth = DashboardAuth(settings.dashboard_password)
    router.register("GET", "/api/auth/status", make_auth_status_handler(dashboard_auth))
    router.register("POST", "/api/auth/login", make_auth_login_handler(dashboard_auth))
    router.register("POST", "/api/auth/logout", make_auth_logout_handler(dashboard_auth))
    router.register_prefix("POST", "/api/tool_approvals/", make_tool_approval_handler(approval_broker))

    # dashboard 静态托管：/ → /dashboard/，/dashboard/* → dashboard 目录
    dashboard_dir = _Path(__file__).resolve().parents[3].parent / "dashboard"
    if dashboard_dir.is_dir():
        router.register("GET", "/", make_root_redirect_handler())
        router.register_prefix("GET", "/dashboard", make_dashboard_handler(dashboard_dir))
        _logger.info("dashboard 静态托管已注册", dir=str(dashboard_dir))
    else:
        _logger.warning("dashboard 目录不存在，跳过静态托管", path=str(dashboard_dir))

    # ─── /api/* 读 endpoints ───
    import time as _time
    start_time = _time.time()
    persona_for_api = loader  # PersonaLoader 一定存在（前面 load 过）
    router.register("GET", "/api/overview", make_overview_handler(
        db, loop, persona_for_api, memdir_loader, claudemd_loader,
        skill_loader, start_time, settings,
    ))
    router.register("GET", "/api/health", make_health_api_handler(health, loop, db))
    router.register("GET", "/api/sessions", make_sessions_handler(db, loop, settings.data_dir))
    router.register_prefix("GET", "/api/sessions/", make_session_messages_handler(settings.data_dir))
    router.register("GET", "/api/tools", make_tools_handler(tool_registry))
    router.register("GET", "/api/tool_calls", make_tool_calls_handler(db, loop))
    router.register("GET", "/api/persona", make_persona_handler(persona_for_api))
    router.register_prefix("GET", "/api/persona/", make_persona_file_handler(persona_for_api))
    router.register("GET", "/api/memory", make_memory_handler(memdir_loader, claudemd_loader))
    router.register_prefix("GET", "/api/memory/", make_memory_file_handler(memdir_loader, claudemd_loader))
    router.register("GET", "/api/skills", make_skills_handler(skill_loader, db, loop))
    # 前缀路由必须在 exact 之后注册（resolve 先查 exact）；handler 内严格校验 /api/skills/{id}/structure 形状
    router.register_prefix("GET", "/api/skills/", make_skill_structure_handler(skill_loader))
    router.register("GET", "/api/channels", make_channels_handler(settings, health))
    router.register("GET", "/api/permissions", make_permissions_handler(settings_loader, db, loop))
    router.register("GET", "/api/settings_json", make_settings_json_handler(settings_loader))
    router.register("GET", "/api/heartbeat", make_heartbeat_list_handler(heartbeat))

    # ─── 写 endpoints ───
    router.register("POST", "/api/sessions/new", make_session_new_handler(session_store))
    router.register_prefix("DELETE", "/api/sessions/", make_session_delete_handler(
        db, loop, session_store, settings.data_dir,
    ))
    router.register_prefix("PUT", "/api/persona/", make_persona_write_handler(persona_for_api))
    if memdir_loader is not None:
        router.register("POST", "/api/memory", make_memory_create_handler(memdir_loader))
        mem_mod = make_memory_modify_handler(memdir_loader)
        router.register_prefix("PUT",    "/api/memory/", mem_mod)
        router.register_prefix("DELETE", "/api/memory/", mem_mod)
    router.register("POST", "/api/skills/reload", make_skills_reload_handler(skill_loader))
    router.register("PUT",  "/api/settings_json", make_settings_json_write_handler(settings_loader))
    router.register("PUT",  "/api/permissions/default_mode", make_permissions_default_mode_handler(settings_loader))
    perm_rule = make_permissions_rule_handler(settings_loader)
    router.register("POST",   "/api/permissions/rule", perm_rule)
    router.register("DELETE", "/api/permissions/rule", perm_rule)
    router.register("POST", "/api/instance/reload", make_instance_reload_handler(
        persona_for_api, memdir_loader, claudemd_loader, skill_loader, settings_loader,
    ))
    # 设置页面读写 .env
    env_file = _Path.cwd() / ".env"
    router.register("GET", "/api/settings", make_get_settings_handler(env_file))
    router.register("PUT", "/api/settings", make_put_settings_handler(env_file))
    # 心跳任务操作：POST /api/heartbeat/{name}/run | /toggle，PUT /api/heartbeat/{name}/config
    _hb_dispatch = make_heartbeat_dispatch_handler(heartbeat, loop)
    router.register_prefix("POST", "/api/heartbeat/", _hb_dispatch)
    router.register_prefix("PUT",  "/api/heartbeat/", _hb_dispatch)

    # 微信扫码连接
    import os as _os
    _store_env = (_os.environ.get("WEIXIN_ACCOUNT_STORE") or "").strip()
    wechat_store = _Path(_store_env) if _store_env else (settings.data_dir / "wechat-account.json")
    wechat_qr_broker = WechatQrBroker(loop=loop, env_path=env_file, store_path=wechat_store)
    router.register("POST", "/api/wechat/qr/start", make_wechat_qr_start_handler(wechat_qr_broker))
    router.register_prefix("GET", "/api/wechat/qr/status", make_wechat_qr_status_handler(wechat_qr_broker))
    router.register("POST", "/api/wechat/qr/cancel", make_wechat_qr_cancel_handler(wechat_qr_broker))

    # wechat 可选启动；webhook 路径挂在同一 web server 上
    wechat_bot: WechatBot | None = None
    wechat_poller: ILinkLongPoller | None = None
    client: ILinkClient | None = None
    if settings.wechat_enabled:
        official_wechat = bool(settings.weixin_account_id.strip() and settings.weixin_token)
        webhook_wechat = bool(settings.ilink_api_key and settings.ilink_webhook_secret)
        if not official_wechat and not webhook_wechat:
            print("wechat_enabled=true 但缺凭据；终止", file=sys.stderr)
            return 78
        if official_wechat:
            # 从 wechat-account.json 取 user_id（若有），用作稳定 X-WECHAT-UIN 种子
            _saved_user_id = ""
            try:
                if wechat_store.is_file():
                    import json as _json
                    _saved = _json.loads(wechat_store.read_text(encoding="utf-8"))
                    if isinstance(_saved, dict):
                        _saved_user_id = str(_saved.get("user_id") or "").strip()
            except Exception:
                _saved_user_id = ""
            client = ILinkClient(
                base_url=settings.weixin_base_url,
                api_key=settings.weixin_token.get_secret_value() if settings.weixin_token else None,
                account_id=settings.weixin_account_id,
                user_id=_saved_user_id,
                timeout=max(settings.weixin_poll_timeout_ms / 1000 + 5, 10),
            )
        else:
            client = ILinkClient(
                base_url=settings.ilink_base_url,
                api_key=settings.ilink_api_key.get_secret_value()
                if settings.ilink_api_key
                else None,
            )
        queue = WechatQueue(db)
        whitelist = WechatWhitelist.from_csv(settings.wechat_whitelist)
        safety = WechatSafety(
            input_blacklist=settings.wechat_input_blacklist.split(","),
            output_blacklist=settings.wechat_output_blacklist.split(","),
        )
        wechat_bot = WechatBot(
            db=db,
            engine=engine,
            client=client,
            queue=queue,
            whitelist=whitelist,
            safety=safety,
            health=health,
            short_term=short_term,
            approval_broker=wechat_approval_broker,
            merge_window_ms=settings.wechat_merge_window_ms,
            merge_window_media_ms=settings.wechat_merge_window_media_ms,
        )
        if official_wechat:
            wechat_poller = ILinkLongPoller(
                db=db,
                client=client,
                account_id=settings.weixin_account_id,
                health=health,
                poll_timeout_ms=settings.weixin_poll_timeout_ms,
                poll_interval_ms=settings.weixin_poll_interval_ms,
            )
        else:
            processor = WechatWebhookProcessor(
                db=db,
                webhook_secret=settings.ilink_webhook_secret.get_secret_value()
                if settings.ilink_webhook_secret
                else "",
                signature_header=settings.ilink_signature_header,
            )
            router.register(
                "POST", "/wechat/webhook", make_webhook_handler(processor.process, loop)
            )
    else:
        health.set("wechat", "disabled")

    server = WebServer(
        host="0.0.0.0",
        port=settings.web_port,
        router=router,
        loop=loop,
        auth=dashboard_auth,
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
    await heartbeat.start()
    server.start()
    if wechat_bot is not None:
        await wechat_bot.start()
    if wechat_poller is not None:
        await wechat_poller.start()

    print("\n── 服务已启动 ──")
    print(f"  HTTP    : http://0.0.0.0:{settings.web_port}")
    print("    POST /chat (SSE) | GET /healthz | GET /metrics")
    if wechat_bot is not None:
        if wechat_poller is not None:
            print("  Wechat  : 已启用，Hermes iLink 长轮询")
        else:
            print("  Wechat  : 已启用，webhook=/wechat/webhook")
    print("  停止：Ctrl+C")

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if wechat_poller is not None:
            await wechat_poller.stop()
        if wechat_bot is not None:
            await wechat_bot.stop()
        if client is not None:
            await client.close()
        server.stop()
        await heartbeat.stop()
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
