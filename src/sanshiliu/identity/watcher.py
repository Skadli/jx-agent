"""人设热重载 watcher；5s 轮询 mtime，变化时 invalidate loader（含 modules）。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from sanshiliu.foundation.logging import get_logger
from sanshiliu.identity.loader import PersonaLoader
from sanshiliu.identity.module_loader import PersonaModuleLoader

_logger = get_logger(__name__)

# 默认轮询周期；prd 2-V4 要求改文件后 10s 内生效，5s 对半即可
_DEFAULT_INTERVAL_SEC = 5.0


class PersonaWatcher:
    """异步 watcher；start() 起后台任务，stop() 优雅退出。

    同时监控 core/*.md（PersonaLoader 管）和 modules/*.md（可选 PersonaModuleLoader 管）。
    任一目录有变更 → invalidate 对应 loader → 下次 get/list 重读。
    """

    def __init__(
        self,
        loader: PersonaLoader,
        *,
        module_loader: PersonaModuleLoader | None = None,
        interval: float = _DEFAULT_INTERVAL_SEC,
        on_change: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._loader = loader
        self._module_loader = module_loader
        self._interval = interval
        self._on_change = on_change
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._last_core_mtimes: dict[str, float] = {}
        self._last_module_mtimes: dict[str, float] = {}

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._last_core_mtimes = self._loader.current_mtimes()
        if self._module_loader is not None:
            self._last_module_mtimes = self._module_loader.current_mtimes()
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="persona-watcher")
        _logger.info(
            "persona watcher 启动",
            interval_sec=self._interval,
            watches_modules=self._module_loader is not None,
        )

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=self._interval + 2.0)
            except TimeoutError:
                self._task.cancel()
        self._task = None
        _logger.info("persona watcher 已停止")

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._interval)
                break
            except TimeoutError:
                pass
            await self._check_once()

    async def _check_once(self) -> None:
        """同时采 core + modules 当前 mtime；任一目录有差异即 invalidate 对应 loader。"""
        any_changed = False

        try:
            cur_core = self._loader.current_mtimes()
        except Exception as exc:
            _logger.error("watcher 采 core mtime 失败（不阻塞）", error=str(exc))
            cur_core = self._last_core_mtimes
        if cur_core != self._last_core_mtimes:
            changed = [n for n, ts in cur_core.items() if self._last_core_mtimes.get(n) != ts]
            removed = [n for n in self._last_core_mtimes if n not in cur_core]
            _logger.info("检测到 persona core 变更", changed=changed, removed=removed)
            self._last_core_mtimes = cur_core
            self._loader.invalidate()
            any_changed = True

        if self._module_loader is not None:
            try:
                cur_mod = self._module_loader.current_mtimes()
            except Exception as exc:
                _logger.error("watcher 采 modules mtime 失败（不阻塞）", error=str(exc))
                cur_mod = self._last_module_mtimes
            if cur_mod != self._last_module_mtimes:
                changed = [n for n, ts in cur_mod.items() if self._last_module_mtimes.get(n) != ts]
                removed = [n for n in self._last_module_mtimes if n not in cur_mod]
                _logger.info("检测到 persona modules 变更", changed=changed, removed=removed)
                self._last_module_mtimes = cur_mod
                self._module_loader.invalidate()
                any_changed = True

        if any_changed and self._on_change is not None:
            try:
                await self._on_change()
            except Exception as exc:
                _logger.error("on_change 回调失败（不阻塞）", error=str(exc))
