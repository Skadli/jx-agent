"""SKILL.md 加载器；扫 3 个目录，同 id 时项目级 > 全局 > 仓库内。"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from sanshiliu.foundation.frontmatter import parse
from sanshiliu.foundation.logging import get_logger
from sanshiliu.skills.types import SkillDef

_logger = get_logger(__name__)

# SKILL.md 文件名固定，与 Claude 一致
_SKILL_FILENAME = "SKILL.md"

# list() 惰性热重载的最小探测间隔：距上次 mtime 扫描不足这么久就直接走缓存，避免每次 list()
# 都全目录 stat（一次 Skill 调用会经 validate+execute 多次 lookup→list）。与 persona watcher 同量级。
_RELOAD_CHECK_INTERVAL_SEC = 2.0


def _description_from_body(body: str, *, limit: int = 100) -> str:
    """缺 description 时从正文取首个"散文"行兜底（跳过空行/代码围栏/纯标题符），截到 limit 字符。

    对齐 Claude Code 的 extractDescriptionFromMarkdown——缺字段不丢 skill，而是兜个能用的描述。
    """
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith("```"):  # 跳过空行与代码围栏，别把 ``` 当描述
            continue
        s = s.lstrip("#").strip()  # 去掉 markdown 标题前缀
        if s:
            return s[:limit]
    return ""


class SkillLoader:
    """扫描 3 个目录并解析 SKILL.md；同名 skill 高优先级先赢。"""

    def __init__(self, dirs: list[Path]) -> None:
        # 调用方按优先级从高到低传入；project_dir 在前，repo_dir 在后
        self._dirs = dirs
        self._cache: list[SkillDef] | None = None
        # 上次 load 时各 SKILL.md 的 mtime 指纹；list() 据此做惰性热重载
        self._cache_mtimes: dict[str, float] = {}
        self._last_check: float = 0.0
        # ThreadingHTTPServer 每请求一线程 + 引擎线程会并发 list()/load()/invalidate()，加锁防竞态
        # （照搬 PersonaLoader：_cache 与 _cache_mtimes 必须原子地一起换，否则读到半新半旧快照）。
        self._lock = threading.Lock()

    def current_mtimes(self) -> dict[str, float]:
        """扫 3 个目录下所有 SKILL.md 的 mtime（路径→mtime），作热重载判定指纹。"""
        out: dict[str, float] = {}
        for root in self._dirs:
            if not root.is_dir():
                continue
            for skill_dir in root.iterdir():
                sf = skill_dir / _SKILL_FILENAME
                try:
                    if skill_dir.is_dir() and sf.is_file():
                        out[str(sf)] = sf.stat().st_mtime
                except OSError:
                    continue
        return out

    def load(self) -> list[SkillDef]:
        with self._lock:
            return self._load_locked()

    def _load_locked(self) -> list[SkillDef]:
        seen: dict[str, SkillDef] = {}
        mtimes: dict[str, float] = {}
        for prio, root in enumerate(self._dirs):
            if not root.is_dir():
                continue
            for skill_dir in sorted(root.iterdir()):
                if not skill_dir.is_dir():
                    continue
                sf = skill_dir / _SKILL_FILENAME
                if not sf.is_file():
                    continue
                # 指纹与内容同源：读之前就记 mtime，且对所有存在的 SKILL.md 都记（含被高优先级
                # 覆盖/解析失败/无效的），口径与 current_mtimes() 一致——否则 list() 永远判"有变更"。
                # 这样也消掉了"先全量读、后再全量 stat"两步之间文件被改导致变更被吞的 TOCTOU 窗口。
                try:
                    mtimes[str(sf)] = sf.stat().st_mtime
                except OSError:
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
                has_name = bool(fm.get("name"))
                has_desc = bool(fm.get("description"))
                body_desc = _description_from_body(parsed.body)
                # 最低有效性闸：name/description 全缺、正文也抠不出一句话 → 不是真 skill（空文件 /
                # 仅无关 frontmatter），跳过别污染发现列表。只缺其一仍按 CC 兜底（目录名 / 正文首行）。
                if not has_name and not has_desc and not body_desc:
                    _logger.warning("SKILL.md 无 name/description/正文，跳过", path=str(sf))
                    continue
                # 与 Claude Code（loadSkillsDir.ts）对齐：缺 name 用目录名兜底、缺 description 取
                # 正文首行兜底，不再因漏字段整份丢弃（此前 anthropology/cryptography 等 5 个 skill 被
                # 跳过、模型根本调不到——旧路径只打 warning、模型层无声）。
                name = (str(fm["name"]).strip() if has_name else "") or skill_id
                description = (
                    (str(fm["description"]).strip() if has_desc else "") or body_desc or skill_id
                )
                kw_raw = fm.get("keywords") or []
                # 对齐 module loader：strip + 滤掉空项（keywords 不进 listing，仅供 dashboard /
                # structure / 成长安装搜索消费，但保持干净）
                keywords = (
                    [str(k).strip() for k in kw_raw if str(k).strip()]
                    if isinstance(kw_raw, list)
                    else []
                )
                seen[skill_id] = SkillDef(
                    id=skill_id,
                    name=name,
                    description=description,
                    keywords=keywords,
                    body=parsed.body,
                    source=sf,
                    priority=prio,
                )
        self._cache = list(seen.values())
        self._cache_mtimes = mtimes
        self._last_check = time.monotonic()
        _logger.info("skills 加载完成", count=len(self._cache), dirs=[str(d) for d in self._dirs])
        return self._cache

    def list(self) -> list[SkillDef]:
        with self._lock:
            if self._cache is None:
                return self._load_locked()
            # 惰性热重载 + 节流：距上次探测不足 _RELOAD_CHECK_INTERVAL_SEC 直接走缓存；否则全目录采
            # mtime 指纹，变了就重读。改/加/删 SKILL.md 在一个探测周期内自动生效，不必重启进程。
            # （取代 persona 的后台 watcher：零额外线程、各通道通用，且不必把 L6 的 loader 注入 L3
            # 的 identity watcher 破坏分层。）
            now = time.monotonic()
            if now - self._last_check < _RELOAD_CHECK_INTERVAL_SEC:
                return self._cache
            self._last_check = now
            if self.current_mtimes() != self._cache_mtimes:
                _logger.info("检测到 SKILL.md 变更，重载 skills")
                return self._load_locked()
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
        with self._lock:
            self._cache = None
            self._cache_mtimes = {}
            self._last_check = 0.0
