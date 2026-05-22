"""identity.watcher 单测：mtime 变化 → invalidate + on_change 触发。"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from sanshiliu.identity.loader import PersonaLoader
from sanshiliu.identity.types import PERSONA_FILES
from sanshiliu.identity.watcher import PersonaWatcher


def _seed_persona(dir_: Path) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    for name in PERSONA_FILES:
        (dir_ / name).write_text(f"# {name}\n占位内容", encoding="utf-8")


async def test_no_change_no_invalidate(tmp_path: Path) -> None:
    _seed_persona(tmp_path)
    loader = PersonaLoader(tmp_path)
    loader.load()
    watcher = PersonaWatcher(loader, interval=0.1)
    await watcher.start()
    # 等两轮，没修改文件，loader 不应失效
    await asyncio.sleep(0.3)
    snap_before = loader.get()
    await asyncio.sleep(0.3)
    snap_after = loader.get()
    assert snap_before is snap_after
    await watcher.stop()


async def test_file_change_triggers_invalidate(tmp_path: Path) -> None:
    """改 root.md 后短时间内 loader 自动失效。"""
    _seed_persona(tmp_path)
    loader = PersonaLoader(tmp_path)
    loader.load()
    watcher = PersonaWatcher(loader, interval=0.1)
    await watcher.start()
    try:
        await asyncio.sleep(0.15)
        snap_before = loader.get()

        # 改 root.md 内容 + 推进 mtime（防止 1s 精度被吃）
        target = tmp_path / "root.md"
        target.write_text("# 新版 root", encoding="utf-8")
        fresh = time.time() + 5
        os.utime(target, (fresh, fresh))

        # 给 watcher 至少 3 轮去发现
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            snap_now = loader.get()
            if snap_now is not snap_before:
                break
            await asyncio.sleep(0.1)
        assert snap_now is not snap_before
        assert "新版 root" in snap_now.sections["root.md"]
    finally:
        await watcher.stop()


async def test_on_change_callback_fires(tmp_path: Path) -> None:
    _seed_persona(tmp_path)
    loader = PersonaLoader(tmp_path)
    loader.load()

    fired = asyncio.Event()

    async def _cb() -> None:
        fired.set()

    watcher = PersonaWatcher(loader, interval=0.1, on_change=_cb)
    await watcher.start()
    try:
        target = tmp_path / "style.md"
        target.write_text("# 新 style", encoding="utf-8")
        fresh = time.time() + 5
        os.utime(target, (fresh, fresh))
        await asyncio.wait_for(fired.wait(), timeout=2.0)
    finally:
        await watcher.stop()


async def test_stop_is_idempotent(tmp_path: Path) -> None:
    _seed_persona(tmp_path)
    loader = PersonaLoader(tmp_path)
    loader.load()
    watcher = PersonaWatcher(loader, interval=0.1)
    await watcher.start()
    await watcher.stop()
    await watcher.stop()  # 第二次 stop 不应抛
