"""成长状态机单测；覆盖 advance / rollback / gate 边界 / load-save round-trip。

风格沿用 tests/test_heartbeat_scheduler.py：纯函数 + 临时文件，不打真 LLM。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sanshiliu.scheduler.growth_state import (
    ChapterRecord,
    GrowthState,
    load_growth_state,
    save_growth_state,
)


def _fresh_state() -> GrowthState:
    # 默认 5 岁起、5 年/章、共 5 章（end_chapter=5）
    return GrowthState()


def test_default_state_starts_at_chapter_zero_age_five() -> None:
    state = _fresh_state()
    assert state.current_chapter == 0
    assert state.age == 5
    assert state.end_chapter == 5
    assert state.active_persona_chapter == 0


def test_next_age_range_tracks_current_chapter() -> None:
    state = _fresh_state()
    assert state.next_age_range() == "5-10"
    state.advance(ChapterRecord(age_range="5-10", summary="第一章"))
    assert state.next_age_range() == "10-15"


def test_advance_pushes_chapter_and_moves_age_and_persona_pointer() -> None:
    state = _fresh_state()
    state.advance(ChapterRecord(age_range="5-10", summary="长成小学生"))
    assert state.current_chapter == 1
    assert state.age == 10
    assert state.active_persona_chapter == 1  # 人格整体演化：激活指针跟到最新章
    assert len(state.chapters) == 1
    assert state.chapters[0].summary == "长成小学生"


def test_can_advance_true_below_end_false_when_frozen() -> None:
    state = _fresh_state()
    # 章 0..4 都可推进
    for i in range(5):
        assert state.can_advance() is True, f"第 {i} 章前应可推进"
        state.advance(ChapterRecord(age_range=f"{5 + i * 5}-{10 + i * 5}", summary=f"第{i + 1}章"))
    # 满 5 章 → 30 岁定格 → 永久 false
    assert state.current_chapter == 5
    assert state.age == 30
    assert state.can_advance() is False


def test_advance_past_end_raises() -> None:
    state = _fresh_state()
    for i in range(5):
        state.advance(ChapterRecord(age_range=f"ch{i}", summary=f"第{i + 1}章"))
    # 满章后再 advance 必须抛，防止写脏数据越过定格
    with pytest.raises(ValueError, match="定格"):
        state.advance(ChapterRecord(age_range="30-35", summary="越界章"))


def test_rollback_resets_active_persona_chapter_without_dropping_history() -> None:
    state = _fresh_state()
    for i in range(3):
        state.advance(ChapterRecord(age_range=f"ch{i}", summary=f"第{i + 1}章"))
    assert state.active_persona_chapter == 3
    state.rollback(1)
    # 仅改激活指针；历史 chapters 不删（dashboard 仍能看全历史）
    assert state.active_persona_chapter == 1
    assert state.current_chapter == 3
    assert len(state.chapters) == 3


def test_rollback_out_of_range_raises() -> None:
    state = _fresh_state()
    state.advance(ChapterRecord(age_range="5-10", summary="第一章"))
    with pytest.raises(ValueError):
        state.rollback(5)  # > current_chapter
    with pytest.raises(ValueError):
        state.rollback(-1)


def test_save_then_load_round_trips_full_state(tmp_path: Path) -> None:
    path = tmp_path / "growth-state.json"
    state = _fresh_state()
    state.advance(ChapterRecord(age_range="5-10", summary="长成小学生", report="汇报A"))
    state.advance(ChapterRecord(age_range="10-15", summary="长成少年", installed_skills=["s1"]))
    save_growth_state(path, state)

    loaded = load_growth_state(path)
    assert loaded.current_chapter == 2
    assert loaded.age == 15
    assert loaded.active_persona_chapter == 2
    assert len(loaded.chapters) == 2
    assert loaded.chapters[0].report == "汇报A"
    assert loaded.chapters[1].installed_skills == ["s1"]


def test_load_missing_file_seeds_from_config(tmp_path: Path) -> None:
    path = tmp_path / "nope.json"
    # 自定义 config：10 岁起、10 年/章、到 30 岁 → (30-10)/10 = 2 章
    loaded = load_growth_state(path, start_age=10, years_per_chapter=10, end_age=30)
    assert loaded.current_chapter == 0
    assert loaded.age == 10
    assert loaded.start_age == 10
    assert loaded.years_per_chapter == 10
    assert loaded.end_chapter == 2


def test_load_corrupt_json_returns_fresh_state(tmp_path: Path) -> None:
    path = tmp_path / "growth-state.json"
    path.write_text("{ this is not valid json", encoding="utf-8")
    # 坏 JSON 不抛，按新状态启动（不让一个坏文件卡死成长线）
    loaded = load_growth_state(path)
    assert loaded.current_chapter == 0
    assert loaded.age == 5
