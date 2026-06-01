"""成长状态机；data/growth-state.json 的 load/save/advance/rollback + gate 判定。

为什么单独成一个纯模块：成长推进是有限状态机（5 岁→30 岁、共 5 章），逻辑必须
可单测、不依赖 LLM / engine / 文件系统副作用。load/save 只碰一个 JSON 文件，advance/
rollback/can_advance 是纯函数式状态变换，便于 mypy strict + pytest 覆盖边界。

状态形状（与 prd R4 / research 01 对齐）：
    {
      "current_chapter": int,        # 已完成章数 0..end_chapter
      "age": int,                    # 当前年龄 = start_age + current_chapter * years_per_chapter
      "active_persona_chapter": int, # 当前激活的人格章（PR2 用；回滚改这个）
      "start_age": int,
      "years_per_chapter": int,
      "end_chapter": int,            # 满此章数永久定格（= (end_age-start_age)/years_per_chapter）
      "chapters": [                  # 每完成一章 append 一条
        {
          "age_range": "5-10",
          "summary": "...",          # 本章传记叙述（结构化输出的 narrative）
          "report": "...",           # 本章汇报（PR3 填充；PR1 先留空串）
          "installed_skills": [],    # 本章自动安装的 skill（PR3 填充）
          "created_at": 1716000000.0
        }
      ]
    }
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sanshiliu.foundation.logging import get_logger

_logger = get_logger(__name__)

# 默认值——首次建状态时 seed；与 config.growth_* 默认一致（5 岁起、5 年/章、30 岁止 → 共 5 章）
_DEFAULT_START_AGE = 5
_DEFAULT_YEARS_PER_CHAPTER = 5
_DEFAULT_END_AGE = 30


@dataclass
class ChapterRecord:
    """一章成长的产物快照；append 进 GrowthState.chapters。"""

    age_range: str
    summary: str
    report: str = ""
    installed_skills: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


@dataclass
class GrowthState:
    """成长有限状态机；current_chapter 是已完成章数，满 end_chapter 定格。"""

    current_chapter: int = 0
    age: int = _DEFAULT_START_AGE
    active_persona_chapter: int = 0
    start_age: int = _DEFAULT_START_AGE
    years_per_chapter: int = _DEFAULT_YEARS_PER_CHAPTER
    # 总章数；由 (end_age - start_age) / years_per_chapter 推出（默认 (30-5)/5 = 5）
    end_chapter: int = (_DEFAULT_END_AGE - _DEFAULT_START_AGE) // _DEFAULT_YEARS_PER_CHAPTER
    chapters: list[ChapterRecord] = field(default_factory=list)

    def can_advance(self) -> bool:
        """gate 核心：已完成章数 < 总章数才放行；满 end_chapter 永久 false（30 岁定格）。

        不含"同日不重复"限制——日级节奏由 scheduler 的 daily_at_hour 保证；手动 run_now
        需要能连推几章以便测试，所以这里只判章数上限。
        """
        return self.current_chapter < self.end_chapter

    def next_age_range(self) -> str:
        """下一章覆盖的年龄段字符串，如当前 current_chapter=0、start=5、step=5 → "5-10"。"""
        lo = self.start_age + self.current_chapter * self.years_per_chapter
        hi = lo + self.years_per_chapter
        return f"{lo}-{hi}"

    def advance(self, chapter_result: ChapterRecord) -> None:
        """推进一章：append 产物、current_chapter+1、age 前移、active_persona_chapter 跟到最新。

        调用方有责任先用 can_advance() 判定；满章后再调会 raise（防止越界写脏数据）。
        """
        if not self.can_advance():
            raise ValueError(
                f"已满 {self.end_chapter} 章（30 岁定格），不能再 advance"
            )
        self.chapters.append(chapter_result)
        self.current_chapter += 1
        self.age = self.start_age + self.current_chapter * self.years_per_chapter
        # 人格整体演化：推进后当前激活人格 = 最新章（PR2 真正写人格快照；这里先维护指针）
        self.active_persona_chapter = self.current_chapter

    def rollback(self, to_chapter: int) -> None:
        """回退到某章人格；重设 active_persona_chapter（不删历史 chapters，保留可追溯）。

        用于 prd R11"回退到某章人格"。仅改激活指针——已写的传记/汇报不抹掉，dashboard
        仍能看全历史；外部 skill 的卸载是二期，这里不碰。
        """
        if not 0 <= to_chapter <= self.current_chapter:
            raise ValueError(
                f"to_chapter 必须在 0..{self.current_chapter}，收到 {to_chapter}"
            )
        self.active_persona_chapter = to_chapter

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _coerce_state(raw: dict[str, Any]) -> GrowthState:
    """把 JSON dict 还原成 GrowthState；缺字段用默认、坏 chapters 跳过。"""
    chapters: list[ChapterRecord] = []
    raw_chapters = raw.get("chapters")
    if isinstance(raw_chapters, list):
        for item in raw_chapters:
            if not isinstance(item, dict):
                continue
            age_range = item.get("age_range")
            summary = item.get("summary")
            if not isinstance(age_range, str) or not isinstance(summary, str):
                continue
            skills_raw = item.get("installed_skills")
            skills = (
                [s for s in skills_raw if isinstance(s, str)]
                if isinstance(skills_raw, list)
                else []
            )
            # 先绑定到局部变量，mypy 才能据 isinstance 收窄类型（item.get 调两次无法收窄）
            report = item.get("report")
            created = item.get("created_at")
            chapters.append(
                ChapterRecord(
                    age_range=age_range,
                    summary=summary,
                    report=report if isinstance(report, str) else "",
                    installed_skills=skills,
                    created_at=float(created) if isinstance(created, int | float) else time.time(),
                )
            )

    def _int(key: str, default: int) -> int:
        v = raw.get(key)
        return v if isinstance(v, int) and not isinstance(v, bool) else default

    return GrowthState(
        current_chapter=_int("current_chapter", 0),
        age=_int("age", _DEFAULT_START_AGE),
        active_persona_chapter=_int("active_persona_chapter", 0),
        start_age=_int("start_age", _DEFAULT_START_AGE),
        years_per_chapter=_int("years_per_chapter", _DEFAULT_YEARS_PER_CHAPTER),
        end_chapter=_int(
            "end_chapter",
            (_DEFAULT_END_AGE - _DEFAULT_START_AGE) // _DEFAULT_YEARS_PER_CHAPTER,
        ),
        chapters=chapters,
    )


def load_growth_state(
    path: Path,
    *,
    start_age: int = _DEFAULT_START_AGE,
    years_per_chapter: int = _DEFAULT_YEARS_PER_CHAPTER,
    end_age: int = _DEFAULT_END_AGE,
) -> GrowthState:
    """读 growth-state.json；不存在/坏 JSON 返回按 config 初始化的全新状态（不抛）。

    config 的 start_age/years_per_chapter/end_age 只在**首次**建状态时 seed；状态文件存在后
    以文件为真相源（避免改 env 把跑到一半的成长线打乱）。
    """
    if not path.is_file():
        end_chapter = (end_age - start_age) // years_per_chapter if years_per_chapter else 0
        return GrowthState(
            age=start_age,
            start_age=start_age,
            years_per_chapter=years_per_chapter,
            end_chapter=max(end_chapter, 0),
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("growth-state.json 解析失败，按新状态启动", path=str(path), error=str(exc))
        end_chapter = (end_age - start_age) // years_per_chapter if years_per_chapter else 0
        return GrowthState(
            age=start_age,
            start_age=start_age,
            years_per_chapter=years_per_chapter,
            end_chapter=max(end_chapter, 0),
        )
    if not isinstance(raw, dict):
        return GrowthState(age=start_age, start_age=start_age, years_per_chapter=years_per_chapter)
    return _coerce_state(raw)


def save_growth_state(path: Path, state: GrowthState) -> None:
    """原子写 growth-state.json：先写 .tmp 再 rename，避免半写文件破坏下次启动。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(path)
    except OSError as exc:
        _logger.error("growth-state.json 写盘失败", path=str(path), error=str(exc))
