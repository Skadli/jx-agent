"""SKILL.md 加载器；扫 3 个目录，同 id 时项目级 > 全局 > 仓库内。"""

from __future__ import annotations

from pathlib import Path

from sanshiliu.foundation.frontmatter import parse
from sanshiliu.foundation.logging import get_logger
from sanshiliu.skills.types import SkillDef

_logger = get_logger(__name__)

# SKILL.md 文件名固定，与 Claude 一致
_SKILL_FILENAME = "SKILL.md"


class SkillLoader:
    """扫描 3 个目录并解析 SKILL.md；同名 skill 高优先级先赢。"""

    def __init__(self, dirs: list[Path]) -> None:
        # 调用方按优先级从高到低传入；project_dir 在前，repo_dir 在后
        self._dirs = dirs
        self._cache: list[SkillDef] | None = None

    def load(self) -> list[SkillDef]:
        seen: dict[str, SkillDef] = {}
        for prio, root in enumerate(self._dirs):
            if not root.is_dir():
                continue
            for skill_dir in sorted(root.iterdir()):
                if not skill_dir.is_dir():
                    continue
                sf = skill_dir / _SKILL_FILENAME
                if not sf.is_file():
                    continue
                skill_id = skill_dir.name
                if skill_id in seen:
                    _logger.debug("skill 已被高优先级目录覆盖", id=skill_id)
                    continue
                try:
                    parsed = parse(sf.read_text(encoding="utf-8"))
                except ValueError as exc:
                    _logger.warning("SKILL.md 解析失败，跳过", path=str(sf), error=str(exc))
                    continue
                fm = parsed.frontmatter
                if "name" not in fm or "description" not in fm:
                    _logger.warning("SKILL.md 缺 name/description，跳过", path=str(sf))
                    continue
                kw_raw = fm.get("keywords") or []
                keywords = [str(k) for k in kw_raw] if isinstance(kw_raw, list) else []
                seen[skill_id] = SkillDef(
                    id=skill_id,
                    name=str(fm["name"]),
                    description=str(fm["description"]),
                    keywords=keywords,
                    body=parsed.body,
                    source=sf,
                    priority=prio,
                )
        self._cache = list(seen.values())
        _logger.info("skills 加载完成", count=len(self._cache), dirs=[str(d) for d in self._dirs])
        return self._cache

    def list(self) -> list[SkillDef]:
        if self._cache is None:
            return self.load()
        return self._cache

    def discover_ids(self) -> set[str]:
        """Parse-free 发现：扫同一批目录，返回"含 SKILL.md 的直接子目录名"集合。

        与 load() 的 id 口径一致（id = 目录名），但**不解析 frontmatter**。供成长 phase-2
        做"装前/装后目录 diff"记账用：装进来但 frontmatter 不合法、load() 会丢弃的 skill，
        在这里仍按"目录已落地"计入（目录是真相源）。读不动某目录则跳过该目录。
        """
        ids: set[str] = set()
        for root in self._dirs:
            if not root.is_dir():
                continue
            for skill_dir in root.iterdir():
                if skill_dir.is_dir() and (skill_dir / _SKILL_FILENAME).is_file():
                    ids.add(skill_dir.name)
        return ids

    def invalidate(self) -> None:
        self._cache = None
