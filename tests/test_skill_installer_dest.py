"""skill-installer 目标目录单测（方案 A·B + R3 调整：装进 jx-agent 用户级全局 skills 目录）。

被测不变量（修复"装到 jx-agent 扫不到的目录→growth diff 必净 0"）：
- 设了 SANSHILIU_SKILLS_DIR_GLOBAL → 用它（支持自定义/绝对路径，~ 会展开）。
- 否则设了 SANSHILIU_HOME_DIR → <home>/skills（跟随自定义 home）。
- 否则默认 → ~/.sanshiliu/skills（expanduser；loader 默认也扫这里）。
- 任何情况都不再回落 ~/.codex/skills。

脚本文件名带连字符（install-skill-from-github.py）且 import 同目录的 github_utils，故用 importlib
按路径加载，并把 scripts 目录放进 sys.path 让 github_utils 可解析。不跑真安装/网络，只验纯函数。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

# 仓库根（tests/ 的上一级）→ skills/skill-installer/scripts/install-skill-from-github.py
_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[1]
    / "skills" / "skill-installer" / "scripts"
)
_SCRIPT_PATH = _SCRIPTS_DIR / "install-skill-from-github.py"


def _load_installer() -> ModuleType:
    """按路径加载带连字符名的安装脚本；scripts 目录入 sys.path 让其 import github_utils 成功。"""
    if str(_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location("install_skill_from_github", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # 3.14 的 @dataclass 在 from __future__ annotations 下要按 cls.__module__ 在 sys.modules
    # 找回模块解析字符串注解；exec 前先登记，否则报 'NoneType' has no attribute '__dict__'。
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_default_dest_is_user_global_skills_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    # 无任何 env → 默认 ~/.sanshiliu/skills（不是 ~/.codex、也不是项目级）——loader 默认会扫这里
    monkeypatch.delenv("SANSHILIU_SKILLS_DIR_GLOBAL", raising=False)
    monkeypatch.delenv("SANSHILIU_HOME_DIR", raising=False)
    mod = _load_installer()

    dest = mod._default_dest()
    assert dest == os.path.join(os.path.expanduser("~"), ".sanshiliu", "skills")
    # 关键回归：绝不再回落到 codex 目录
    assert ".codex" not in dest


def test_default_dest_follows_home_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # 设了 SANSHILIU_HOME_DIR（但没 GLOBAL）→ <home>/skills，跟随自定义 home（不碰真 home）
    monkeypatch.delenv("SANSHILIU_SKILLS_DIR_GLOBAL", raising=False)
    monkeypatch.setenv("SANSHILIU_HOME_DIR", str(tmp_path))
    mod = _load_installer()

    assert mod._default_dest() == os.path.join(str(tmp_path), "skills")


def test_default_dest_honors_global_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # 设了 SANSHILIU_SKILLS_DIR_GLOBAL → 优先用它（与 jx-agent config 的同名 env 对齐）
    custom = tmp_path / "twin" / "skills"
    monkeypatch.setenv("SANSHILIU_SKILLS_DIR_GLOBAL", str(custom))
    monkeypatch.setenv("SANSHILIU_HOME_DIR", str(tmp_path / "ignored"))
    mod = _load_installer()

    # GLOBAL 优先于 HOME_DIR
    assert mod._default_dest() == str(custom)
