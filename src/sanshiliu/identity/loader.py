"""人设加载器；扫描 persona/*.md 拼装 PersonaSnapshot，缺文件报错含字段名。"""

from __future__ import annotations

import time
from pathlib import Path
from threading import Lock

from sanshiliu.foundation.errors import ConfigError
from sanshiliu.foundation.logging import get_logger
from sanshiliu.identity.types import PERSONA_FILES, PersonaSnapshot

_logger = get_logger(__name__)


class PersonaLoader:
    """加载器；线程安全，watcher 调 invalidate 后下次 get 重新读盘。"""

    def __init__(self, persona_dir: Path) -> None:
        self._persona_dir = persona_dir
        self._snapshot: PersonaSnapshot | None = None
        self._lock = Lock()

    @property
    def persona_dir(self) -> Path:
        return self._persona_dir

    def file_paths(self) -> list[Path]:
        """返回所有应被监控的 persona md 路径。"""
        return [self._persona_dir / name for name in PERSONA_FILES]

    def load(self) -> PersonaSnapshot:
        """强制从磁盘读盘并刷新缓存；缺任意一份 md 抛 ConfigError 含字段名。"""
        missing: list[str] = []
        sections: dict[str, str] = {}
        mtimes: dict[str, float] = {}

        for name in PERSONA_FILES:
            path = self._persona_dir / name
            if not path.is_file():
                missing.append(name)
                continue
            sections[name] = path.read_text(encoding="utf-8").strip()
            mtimes[name] = path.stat().st_mtime

        if missing:
            raise ConfigError(
                f"persona 目录缺少必需的 md 文件：{', '.join(missing)}\n"
                f"  搜索目录：{self._persona_dir}\n"
                "  解决：切到含 persona/ 的工作目录，或设环境变量 SANSHILIU_PERSONA_DIR=/path/to/persona",
            )

        snap = PersonaSnapshot(
            sections=sections,
            mtimes=mtimes,
            loaded_at=time.time(),
            persona_dir=self._persona_dir,
        )
        with self._lock:
            self._snapshot = snap
        _logger.info(
            "人设已加载",
            files=len(sections),
            total_chars=snap.total_chars(),
            dir=str(self._persona_dir),
        )
        return snap

    def get(self) -> PersonaSnapshot:
        """获取当前快照；从未加载过则立即 load。"""
        with self._lock:
            snap = self._snapshot
        return snap if snap is not None else self.load()

    def invalidate(self) -> None:
        """让下次 get 强制重读；watcher 检测到 mtime 变化时调用。"""
        with self._lock:
            self._snapshot = None
        _logger.info("人设缓存已失效，下次 get 会重读")

    def current_mtimes(self) -> dict[str, float]:
        """采当前磁盘上 5 个文件的 mtime 快照（不读内容）；watcher 用来对比。"""
        out: dict[str, float] = {}
        for name in PERSONA_FILES:
            path = self._persona_dir / name
            if path.is_file():
                out[name] = path.stat().st_mtime
        return out
