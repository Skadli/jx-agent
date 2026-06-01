"""skill-installer 目标目录单测（方案 A·B：装进 jx-agent 项目 skills 目录，不再是 ~/.codex/skills）。

被测不变量（修复"装到 jx-agent 扫不到的目录→growth diff 必净 0"）：
- 默认（无 env）→ _default_dest() == ./.sanshiliu/skills（相对 cwd=仓库根，loader 真正会扫）。
- 设了 SANSHILIU_SKILLS_DIR_PROJECT → 用它（支持自定义/绝对路径，~ 会展开）。

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


def test_default_dest_is_project_skills_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    # 无 env → 默认 ./.sanshiliu/skills（不是 ~/.codex/skills）——loader 真正会扫的目录
    monkeypatch.delenv("SANSHILIU_SKILLS_DIR_PROJECT", raising=False)
    mod = _load_installer()

    dest = mod._default_dest()
    assert dest == os.path.join(".sanshiliu", "skills")
    # 关键回归：绝不再回落到 codex 目录
    assert ".codex" not in dest


def test_default_dest_honors_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    # 设了 SANSHILIU_SKILLS_DIR_PROJECT → 用它（与 jx-agent config 的同名 env 对齐）
    monkeypatch.setenv("SANSHILIU_SKILLS_DIR_PROJECT", "/srv/twin/.sanshiliu/skills")
    mod = _load_installer()

    assert mod._default_dest() == "/srv/twin/.sanshiliu/skills"
