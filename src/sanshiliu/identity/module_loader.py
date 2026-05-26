"""persona modules 加载器；扫 persona/modules/*.md，frontmatter 必含 name/description。"""

from __future__ import annotations

from pathlib import Path
from threading import Lock

from sanshiliu.foundation.frontmatter import parse
from sanshiliu.foundation.logging import get_logger
from sanshiliu.identity.module_types import PersonaModule
from sanshiliu.identity.types import MODULES_DIRNAME

_logger = get_logger(__name__)


class PersonaModuleLoader:
    """扫 persona/modules/*.md → list[PersonaModule]。线程安全；watcher 用 invalidate 触发重读。

    modules 目录不存在 / 为空：返回空列表，**不**报错（modules 是可选系统）。
    """

    def __init__(self, persona_dir: Path) -> None:
        self._persona_dir = persona_dir
        self._cache: list[PersonaModule] | None = None
        self._lock = Lock()

    @property
    def modules_dir(self) -> Path:
        return self._persona_dir / MODULES_DIRNAME

    def file_paths(self) -> list[Path]:
        """返回 modules/*.md 的路径列表（按字母序）。"""
        md = self.modules_dir
        if not md.is_dir():
            return []
        return sorted(p for p in md.glob("*.md") if p.is_file())

    def load(self) -> list[PersonaModule]:
        """读盘 + 解析 frontmatter；frontmatter 缺 name/description 时跳过并 warn。"""
        out: list[PersonaModule] = []
        for p in self.file_paths():
            try:
                parsed = parse(p.read_text(encoding="utf-8"))
            except ValueError as exc:
                _logger.warning("persona module frontmatter 解析失败，跳过", path=str(p), error=str(exc))
                continue
            fm = parsed.frontmatter
            if "name" not in fm or "description" not in fm:
                _logger.warning("persona module 缺 name/description，跳过", path=str(p))
                continue
            kw_raw = fm.get("trigger_keywords") or []
            keywords = [str(k).strip() for k in kw_raw if str(k).strip()] if isinstance(kw_raw, list) else []
            out.append(PersonaModule(
                id=p.stem,
                name=str(fm["name"]),
                description=str(fm["description"]),
                trigger_keywords=keywords,
                body=parsed.body,
                source=p,
                mtime=p.stat().st_mtime,
            ))
        with self._lock:
            self._cache = out
        _logger.info("persona modules 已加载", count=len(out), dir=str(self.modules_dir))
        return out

    def list(self) -> list[PersonaModule]:
        with self._lock:
            cached = self._cache
        return cached if cached is not None else self.load()

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None
        _logger.info("persona modules 缓存已失效，下次 list 会重读")

    def lookup(self, name_or_id: str) -> PersonaModule | None:
        """按 name 或 id 找单个 module；LoadPersonaModule 工具调用。"""
        for m in self.list():
            if m.id == name_or_id or m.name == name_or_id:
                return m
        return None

    def current_mtimes(self) -> dict[str, float]:
        """采当前磁盘上 modules/*.md 的 mtime；watcher 用来对比。"""
        out: dict[str, float] = {}
        for p in self.file_paths():
            out[p.name] = p.stat().st_mtime
        return out
