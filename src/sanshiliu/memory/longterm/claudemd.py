"""CLAUDE.md 加载器；读项目级 + 全局两份，按全局→项目顺序拼装。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sanshiliu.foundation.logging import get_logger

_logger = get_logger(__name__)

# 与 Claude 协议一致的文件名；保持大写
_CLAUDE_MD = "CLAUDE.md"


@dataclass(frozen=True)
class ClaudeMdSnapshot:
    """两份 CLAUDE.md 的合并结果；空文件 → 空字符串。"""

    global_text: str
    project_text: str
    global_path: Path
    project_path: Path

    def assembled(self) -> str:
        """全局在前，项目在后；纯结构胶水分隔。"""
        parts = [p for p in (self.global_text.strip(), self.project_text.strip()) if p]
        return "\n\n---\n\n".join(parts)

    def total_chars(self) -> int:
        return len(self.global_text) + len(self.project_text)


class ClaudeMdLoader:
    """启动期一次性读取；运行期 invalidate 后下次 get 重读。"""

    def __init__(self, *, global_home: Path, project_cwd: Path) -> None:
        self._global_path = global_home / _CLAUDE_MD
        self._project_path = project_cwd / _CLAUDE_MD
        self._snapshot: ClaudeMdSnapshot | None = None

    def load(self) -> ClaudeMdSnapshot:
        gtext = self._read_if_exists(self._global_path)
        ptext = self._read_if_exists(self._project_path)
        snap = ClaudeMdSnapshot(
            global_text=gtext,
            project_text=ptext,
            global_path=self._global_path,
            project_path=self._project_path,
        )
        self._snapshot = snap
        _logger.info(
            "CLAUDE.md 加载",
            global_chars=len(gtext), project_chars=len(ptext),
            global_path=str(self._global_path), project_path=str(self._project_path),
        )
        return snap

    def get(self) -> ClaudeMdSnapshot:
        return self._snapshot if self._snapshot is not None else self.load()

    def invalidate(self) -> None:
        self._snapshot = None

    @staticmethod
    def _read_if_exists(path: Path) -> str:
        if not path.is_file():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            _logger.warning("CLAUDE.md 读失败（继续不带）", path=str(path), error=str(exc))
            return ""
