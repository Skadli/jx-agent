"""identity.loader 单测：扫盘、缺文件报错、缓存与失效。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from sanshiliu.foundation.errors import ConfigError
from sanshiliu.identity.loader import PersonaLoader
from sanshiliu.identity.types import PERSONA_FILES


def _write_full_persona(dir_: Path) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    for name in PERSONA_FILES:
        (dir_ / name).write_text(f"# {name}\n本段是占位内容。", encoding="utf-8")


def test_load_full_set_ok(tmp_path: Path) -> None:
    _write_full_persona(tmp_path)
    loader = PersonaLoader(tmp_path)
    snap = loader.load()
    assert set(snap.sections.keys()) == set(PERSONA_FILES)
    assert all(snap.sections[n].startswith(f"# {n}") for n in PERSONA_FILES)
    assert snap.total_chars() > 0


def test_load_missing_file_raises_with_field_name(tmp_path: Path) -> None:
    """V-2：缺任一 md 启动报错且错误含字段名。"""
    _write_full_persona(tmp_path)
    (tmp_path / "root.md").unlink()
    loader = PersonaLoader(tmp_path)
    with pytest.raises(ConfigError) as exc_info:
        loader.load()
    assert "root.md" in str(exc_info.value)


def test_load_all_missing_lists_all(tmp_path: Path) -> None:
    loader = PersonaLoader(tmp_path)
    with pytest.raises(ConfigError) as exc_info:
        loader.load()
    msg = str(exc_info.value)
    for name in PERSONA_FILES:
        assert name in msg


def test_get_caches_and_invalidate_reloads(tmp_path: Path) -> None:
    _write_full_persona(tmp_path)
    loader = PersonaLoader(tmp_path)
    s1 = loader.get()
    s2 = loader.get()
    assert s1 is s2  # 同一对象，证明走了缓存

    (tmp_path / "root.md").write_text("# 新内容", encoding="utf-8")
    loader.invalidate()
    s3 = loader.get()
    assert s3 is not s1
    assert "新内容" in s3.sections["root.md"]


def test_to_system_prompt_concatenates_in_order(tmp_path: Path) -> None:
    """V-3 前置：拼接顺序与 PERSONA_FILES 一致，且各段内容都出现。"""
    _write_full_persona(tmp_path)
    snap = PersonaLoader(tmp_path).load()
    prompt = snap.to_system_prompt()
    pos_prev = -1
    for name in PERSONA_FILES:
        pos = prompt.find(f"# {name}")
        assert pos > pos_prev, f"{name} 顺序错位"
        pos_prev = pos


def test_current_mtimes_returns_only_existing(tmp_path: Path) -> None:
    _write_full_persona(tmp_path)
    (tmp_path / "examples.md").unlink()
    loader = PersonaLoader(tmp_path)
    mtimes = loader.current_mtimes()
    assert "examples.md" not in mtimes
    assert len(mtimes) == len(PERSONA_FILES) - 1


def test_snapshot_total_chars_matches_concatenation(tmp_path: Path) -> None:
    _write_full_persona(tmp_path)
    snap = PersonaLoader(tmp_path).load()
    assert snap.total_chars() == sum(len(s) for s in snap.sections.values())


def test_snapshot_latest_mtime_picks_max(tmp_path: Path) -> None:
    _write_full_persona(tmp_path)
    # 手动让 examples.md mtime 比其他文件新
    target = tmp_path / "examples.md"
    target.write_text("new", encoding="utf-8")
    fresh = target.stat().st_mtime + 10
    import os

    os.utime(target, (fresh, fresh))
    snap = PersonaLoader(tmp_path).load()
    assert snap.latest_mtime() == pytest.approx(fresh, abs=1.0)
    assert time.time() - snap.loaded_at < 5  # 烟测时间戳合理
