"""人设加载器；扫描 persona/core/*.md（按字母序）拼装 PersonaSnapshot。

旧布局兼容：检测到 persona_dir 根下有 root.md/personality.md/beliefs.md/style.md/examples.md
但 core/ 不存在时，抛 ConfigError 并给迁移指引。
"""

from __future__ import annotations

import time
from pathlib import Path
from threading import Lock

from sanshiliu.foundation.errors import ConfigError
from sanshiliu.foundation.logging import get_logger
from sanshiliu.identity.types import CORE_DIRNAME, PersonaSnapshot

_logger = get_logger(__name__)

# 旧布局检测用的文件名（任一存在 + 缺 core/ 即触发迁移提示）
_LEGACY_FILENAMES: tuple[str, ...] = (
    "root.md", "personality.md", "beliefs.md", "style.md", "examples.md",
)


class PersonaLoader:
    """加载器；线程安全，watcher 调 invalidate 后下次 get 重新读盘。"""

    def __init__(self, persona_dir: Path) -> None:
        self._persona_dir = persona_dir
        self._snapshot: PersonaSnapshot | None = None
        self._lock = Lock()

    @property
    def persona_dir(self) -> Path:
        return self._persona_dir

    @property
    def core_dir(self) -> Path:
        return self._persona_dir / CORE_DIRNAME

    def file_paths(self) -> list[Path]:
        """返回 core/ 下所有应被监控的 md 路径（按字母序）。"""
        core = self.core_dir
        if not core.is_dir():
            return []
        return sorted(p for p in core.glob("*.md") if p.is_file())

    def load(self) -> PersonaSnapshot:
        """强制从磁盘读盘并刷新缓存；core/ 缺失或为空抛 ConfigError 并附迁移指引。"""
        core = self.core_dir
        if not core.is_dir():
            self._raise_missing_core()

        md_paths = sorted(p for p in core.glob("*.md") if p.is_file())
        if not md_paths:
            raise ConfigError(
                f"persona/core/ 目录为空：{core}\n"
                "  至少需要一份 .md 文件（推荐 identity.md / style.md / personality.md / beliefs.md / fewshot_short.md）",
            )

        sections: dict[str, str] = {}
        mtimes: dict[str, float] = {}
        for p in md_paths:
            sections[p.name] = p.read_text(encoding="utf-8").strip()
            mtimes[p.name] = p.stat().st_mtime

        snap = PersonaSnapshot(
            sections=sections,
            mtimes=mtimes,
            loaded_at=time.time(),
            persona_dir=self._persona_dir,
            file_order=tuple(p.name for p in md_paths),
        )
        with self._lock:
            self._snapshot = snap
        _logger.info(
            "人设已加载",
            files=len(sections),
            total_chars=snap.total_chars(),
            dir=str(core),
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
        """采当前磁盘上 core/*.md 的 mtime 快照（不读内容）；watcher 用来对比。"""
        core = self.core_dir
        if not core.is_dir():
            return {}
        out: dict[str, float] = {}
        for p in core.glob("*.md"):
            if p.is_file():
                out[p.name] = p.stat().st_mtime
        return out

    def _raise_missing_core(self) -> None:
        """core/ 缺失：根据是否有旧布局文件，给两种不同的错误信息。"""
        legacy_present = [
            name for name in _LEGACY_FILENAMES
            if (self._persona_dir / name).is_file()
        ]
        if legacy_present:
            raise ConfigError(
                f"检测到旧 persona 布局（{self._persona_dir} 下的根 md），"
                "本版本已迁移到 core/ + modules/ 结构。\n"
                f"  发现的旧文件：{', '.join(legacy_present)}\n"
                "  迁移指引：\n"
                "    1. 新建子目录 persona/core/ 和 persona/modules/\n"
                "    2. 把核心人格（身份/性格/价值观/风格/短样本）整理后放进 core/*.md\n"
                "    3. 把节目知识/数据/长样本拆成多个 modules/<name>.md，"
                "frontmatter 含 name/description/trigger_keywords\n"
                "    4. 删除旧根 md\n"
                "  参考默认实现：项目自带的 persona/core/ + persona/modules/",
            )
        raise ConfigError(
            f"persona/core/ 目录不存在：{self.core_dir}\n"
            f"  搜索的 persona 根：{self._persona_dir}\n"
            "  解决：切到含 persona/core/ 的工作目录，或设环境变量 "
            "SANSHILIU_PERSONA_DIR=/path/to/persona",
        )
