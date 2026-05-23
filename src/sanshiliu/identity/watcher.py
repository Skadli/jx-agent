"""人设热重载 watcher；5s 轮询 mtime，变化时 invalidate loader。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from sanshiliu.foundation.logging import get_logger
from sanshiliu.identity.loader import PersonaLoader

_logger = get_logger(__name__)

# 默认轮询周期；prd 2-V4 要求改文件后 10s 内生效，5s 对半即可
_DEFAULT_INTERVAL_SEC = 5.0


class PersonaWatcher:
    """异步 watcher；start() 起后台任务，stop() 优雅退出。"""

    def __init__(
        self,
        loader: PersonaLoader,
        *,
        interval: float = _DEFAULT_INTERVAL_SEC,
        on_change: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._loader = loader
        self._interval = interval
        self._on_change = on_change
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._last_mtimes: dict[str, float] = {}

    async def start(self) -> None:
        """启动后台轮询任务；幂等。"""
        if self._task is not None and not self._task.done():
            return
        # 取首次基线，避免启动瞬间就误报变更
        self._last_mtimes = self._loader.current_mtimes()
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="persona-watcher")
        _logger.info("persona watcher 启动", interval_sec=self._interval)

    async def stop(self) -> None:
        """请求停止并等待任务结束。"""
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
        """采新 mtime，与基线对比；任何差异即触发 invalidate + on_change。"""
        try:
            current = self._loader.current_mtimes()
        except Exception as exc:
            _logger.error("watcher 采 mtime 失败（不阻塞）", error=str(exc))
            return

        if current != self._last_mtimes:
            changed = [
                name for name, ts in current.items()
                if self._last_mtimes.get(name) != ts
            ]
            _logger.info("检测到 persona 变更", files=changed)
            self._last_mtimes = current
            self._loader.invalidate()
            if self._on_change is not None:
                try:
                    await self._on_change()
                except Exception as exc:
                    _logger.error("on_change 回调失败（不阻塞）", error=str(exc))
