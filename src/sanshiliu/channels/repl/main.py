"""REPL 通道：stdlib input() 起最小交互。

支持命令：
- ``/quit`` / ``/exit`` 退出
- ``/stats`` 看本会话 token / 成本汇总
- ``/help``  显示帮助
- 其他文本：当作用户消息送入引擎

设计：
- input() 是阻塞的，用 ``asyncio.to_thread`` 包成 async-friendly。
- Ctrl+C 不让 traceback 直接喷脸——上层 main() 捕获 KeyboardInterrupt。
"""

from __future__ import annotations

import asyncio
import sys
from typing import NoReturn

from sanshiliu import __version__
from sanshiliu.engine.loop import ConversationEngine
from sanshiliu.engine.session import Session
from sanshiliu.foundation.config import get_settings
from sanshiliu.foundation.errors import ConfigError, LLMError, SanshiliuError
from sanshiliu.foundation.logging import configure_logging, get_logger
from sanshiliu.llm.client import LLMClient
from sanshiliu.storage.db import Database, get_database

_logger = get_logger(__name__)

_BANNER = """
╔══════════════════════════════════════════╗
║  三十六贱笑 (Sanshiliu Jianxiao) v{version:<7s}║
║  Phase 1 · 核心引擎                      ║
╠══════════════════════════════════════════╣
║  Model: {model:<33s}║
║  Base : {base:<33s}║
╠══════════════════════════════════════════╣
║  命令: /quit /stats /help                ║
╚══════════════════════════════════════════╝
"""


async def _read_line(prompt: str) -> str:
    """input() 的 async 版本——阻塞读放到线程池，避免堵 event loop。"""
    return await asyncio.to_thread(input, prompt)


async def _print_stats(db: Database, session: Session) -> None:
    """``/stats`` 命令——读 llm_calls 汇总，打印到屏幕。"""
    stats = await db.get_session_stats(session.session_id)
    print(
        "── 本会话统计 ──\n"
        f"  调用次数  : {stats['calls']}\n"
        f"  输入 token: {stats['input_tokens']}\n"
        f"  输出 token: {stats['output_tokens']}\n"
        f"  累计成本  : ￥{stats['cost_cny']:.4f}\n"
        f"  消息条数  : {len(session.messages)}（含 system）\n"
    )


def _print_help() -> None:
    print(
        "── 命令 ──\n"
        "  /quit /exit  退出\n"
        "  /stats       本会话 token/成本汇总\n"
        "  /help        显示本帮助\n"
        "  其他输入     发给 agent\n"
    )


async def run_repl() -> int:
    """REPL 主循环。返回 shell 退出码。"""
    # 配置 + 日志
    try:
        settings = get_settings()
    except Exception as exc:
        # 缺 OPENAI_API_KEY 等会走到这里——错误信息已含字段名（验收 1-V2）
        print(f"配置加载失败：{exc}", file=sys.stderr)
        return 78  # EX_CONFIG

    configure_logging(log_level=settings.log_level, log_dir=settings.data_dir / "logs")

    db = await get_database(settings.data_dir / "sanshiliu.db")
    llm = LLMClient(
        api_key=settings.openai_api_key.get_secret_value(),
        base_url=settings.openai_base_url,
        model=settings.openai_model,
        db=db,
    )
    engine = ConversationEngine(llm=llm, db=db)
    session = Session.new(channel="repl")

    print(_BANNER.format(version=__version__, model=settings.openai_model, base=settings.openai_base_url))
    _logger.info("REPL 启动", session_id=session.session_id, model=settings.openai_model)

    try:
        while True:
            try:
                user_input = await _read_line("\n你> ")
            except EOFError:
                break
            user_input = user_input.strip()
            if not user_input:
                continue

            # 内置命令
            cmd = user_input.lower()
            if cmd in {"/quit", "/exit"}:
                break
            if cmd == "/stats":
                await _print_stats(db, session)
                continue
            if cmd == "/help":
                _print_help()
                continue

            # 业务对话：流式输出
            print("\n贱笑> ", end="", flush=True)
            try:
                async for delta in engine.stream_turn(session, user_input):
                    print(delta.text, end="", flush=True)
                print()  # 换行
            except LLMError as exc:
                # LLM 层错误：报警但不退出 REPL
                print(f"\n[LLM 错误] {exc}", file=sys.stderr)
                _logger.error("LLM 调用失败", error=str(exc), session_id=session.session_id)
            except SanshiliuError as exc:
                print(f"\n[系统错误] {exc}", file=sys.stderr)
                _logger.error("系统异常", error=str(exc), session_id=session.session_id)
    finally:
        await llm.close()
        await db.close()
        _logger.info("REPL 结束", session_id=session.session_id)

    return 0


def run_repl_sync() -> int:
    """sync 入口——给 cli.main / __main__.py 用。"""
    try:
        return asyncio.run(run_repl())
    except KeyboardInterrupt:
        print("\n(已退出)")
        return 130  # 128 + SIGINT
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 78


def _unreachable() -> NoReturn:  # pragma: no cover
    raise RuntimeError("不应到达")
