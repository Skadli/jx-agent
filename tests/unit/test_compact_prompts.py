"""prompts/*.md 加载器单测。"""

from __future__ import annotations

from pathlib import Path

import pytest

from sanshiliu.context.prompts import COMPACT_FILE, MICROCOMPACT_FILE, load_compact_prompts
from sanshiliu.foundation.errors import ConfigError


def _seed(dir_: Path) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / COMPACT_FILE).write_text("# compact 指令内容", encoding="utf-8")
    (dir_ / MICROCOMPACT_FILE).write_text("# microcompact 指令内容", encoding="utf-8")


def test_load_full_set_ok(tmp_path: Path) -> None:
    _seed(tmp_path)
    p = load_compact_prompts(tmp_path)
    assert "compact 指令内容" in p.compact_instruction
    assert "microcompact 指令内容" in p.microcompact_instruction
    assert p.prompts_dir == tmp_path


def test_load_missing_lists_files_in_error(tmp_path: Path) -> None:
    """缺文件错误信息含文件名。"""
    with pytest.raises(ConfigError) as exc_info:
        load_compact_prompts(tmp_path)
    msg = str(exc_info.value)
    assert COMPACT_FILE in msg
    assert MICROCOMPACT_FILE in msg


def test_load_partial_missing(tmp_path: Path) -> None:
    _seed(tmp_path)
    (tmp_path / COMPACT_FILE).unlink()
    with pytest.raises(ConfigError) as exc_info:
        load_compact_prompts(tmp_path)
    assert COMPACT_FILE in str(exc_info.value)
    assert MICROCOMPACT_FILE not in str(exc_info.value)


def test_load_error_includes_solution_hint(tmp_path: Path) -> None:
    """V-2 类似要求：错误信息要给一条用户能照做的解决路径。"""
    with pytest.raises(ConfigError) as exc_info:
        load_compact_prompts(tmp_path)
    assert "SANSHILIU_PROMPTS_DIR" in str(exc_info.value)
