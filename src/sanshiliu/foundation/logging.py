"""structlog 初始化：控制台彩色 + JSONL 文件双输出。

设计目标：
- 第三方库（openai/httpx 等）的 stdlib logging 也走同一通道，避免双轨。
- 文件输出 JSONL，按 RotatingFileHandler 切分；落盘位置在 ``data_dir/logs/``。
- 控制台用人类友好的 dev renderer；CI / 生产可后续切 JSON。

约定：
- 业务代码用 ``logger = get_logger(__name__)``，不要 ``logging.getLogger``。
- 关键决策点（LLM 调用、权限拒绝、compact 触发）必须打 INFO 及以上。
- 调试 trace 用 ``logger.debug``，默认级别 INFO 不输出。
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Any

import structlog
from structlog.types import EventDict, Processor

_INITIALIZED = False


def _add_app_context(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
    """统一在每条日志加 app 字段；查日志时一眼分辨来源。"""
    event_dict.setdefault("app", "sanshiliu")
    return event_dict


def configure_logging(
    *,
    log_level: str = "INFO",
    log_dir: Path | None = None,
    json_console: bool = False,
) -> None:
    """初始化 structlog + stdlib logging。

    幂等：重复调用只生效一次。测试中需要重置：调用 :func:`reset_logging`。

    :param log_level: DEBUG / INFO / WARNING / ERROR / CRITICAL
    :param log_dir: JSONL 日志目录；None 表示不写文件，仅控制台
    :param json_console: True 时控制台也输出 JSON（适合 CI / 容器）
    """
    global _INITIALIZED
    if _INITIALIZED:
        return

    level = getattr(logging, log_level.upper(), logging.INFO)

    # ── structlog 处理链 ──
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=False),
        _add_app_context,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # ── stdlib root logger ──
    root = logging.getLogger()
    root.setLevel(level)
    # 清掉默认 handler 避免双输出
    for h in list(root.handlers):
        root.removeHandler(h)

    # 控制台 handler
    console_renderer: Processor = (
        structlog.processors.JSONRenderer()
        if json_console
        else structlog.dev.ConsoleRenderer(colors=True)
    )
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                console_renderer,
            ],
        )
    )
    root.addHandler(console_handler)

    # JSONL 文件 handler（可选）
    if log_dir is not None:
        log_dir = Path(log_dir).expanduser().resolve()
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "sanshiliu.jsonl",
            maxBytes=50 * 1024 * 1024,  # 50 MB / 文件
            backupCount=10,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                foreign_pre_chain=shared_processors,
                processors=[
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.processors.JSONRenderer(),
                ],
            )
        )
        root.addHandler(file_handler)

    # 嘈杂第三方库降级，避免 INFO 级别被 httpx 心跳刷屏
    for noisy in ("httpx", "httpcore", "openai._base_client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _INITIALIZED = True


def reset_logging() -> None:
    """测试用：复位初始化状态。"""
    global _INITIALIZED
    _INITIALIZED = False
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """业务代码统一入口；未初始化时自动 lazy 初始化（保守缺省）。"""
    if not _INITIALIZED:
        configure_logging()
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
