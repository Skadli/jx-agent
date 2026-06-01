"""SkillLoader 3 级目录扫描单测（R3 配套：新增 global 目录后，loader 必须真扫它且优先级正确）。

被测不变量（不做则 skill-installer 装到 ~/.sanshiliu/skills 也加载不到、growth diff 仍净 0）：
- global 目录里独有的 skill 会被加载（loader 真扫了它，不是只扫 project+repo）。
- 同 id 时优先级 project > global > repo：高优先级目录的那份赢（用 source 路径确认是哪一份）。

不打网络/LLM，只往临时目录写最小合法 SKILL.md 后断言 loader 结果。风格对齐 test_growth_skill.py。
"""

from __future__ import annotations

from pathlib import Path

from sanshiliu.skills.loader import SkillLoader


def _write_skill(skills_dir: Path, skill_id: str, name: str) -> Path:
    """往 skills_dir/<id>/SKILL.md 写一份最小合法 skill（frontmatter 需含 name + description）。"""
    d = skills_dir / skill_id
    d.mkdir(parents=True, exist_ok=True)
    sf = d / "SKILL.md"
    sf.write_text(
        f"---\nname: {name}\ndescription: 测试用 {name}。\n---\n\n正文。\n",
        encoding="utf-8",
    )
    return sf


def test_global_dir_is_scanned(tmp_path: Path) -> None:
    # global 目录里独有的 skill 必须被加载——证明 loader 真把 global 目录扫进去了
    project = tmp_path / "project"
    global_dir = tmp_path / "global"
    repo = tmp_path / "repo"
    _write_skill(global_dir, "only-in-global", "全局独有")

    loader = SkillLoader([project, global_dir, repo])
    skills = loader.load()

    assert {s.id for s in skills} == {"only-in-global"}


def test_priority_project_over_global_over_repo(tmp_path: Path) -> None:
    # 同 id 同时存在于三处 → project 那份赢（用 source 路径确认来自 project 目录）
    project = tmp_path / "project"
    global_dir = tmp_path / "global"
    repo = tmp_path / "repo"
    _write_skill(project, "dup", "来自项目")
    _write_skill(global_dir, "dup", "来自全局")
    _write_skill(repo, "dup", "来自仓库")

    loader = SkillLoader([project, global_dir, repo])
    skills = loader.load()

    assert len(skills) == 1
    dup = skills[0]
    assert dup.name == "来自项目"
    assert dup.source == project / "dup" / "SKILL.md"


def test_global_shadows_repo_when_no_project(tmp_path: Path) -> None:
    # 同 id 只在 global 与 repo → global 赢（global 优先级高于 repo）
    project = tmp_path / "project"
    global_dir = tmp_path / "global"
    repo = tmp_path / "repo"
    _write_skill(global_dir, "dup", "来自全局")
    _write_skill(repo, "dup", "来自仓库")

    loader = SkillLoader([project, global_dir, repo])
    skills = loader.load()

    assert len(skills) == 1
    assert skills[0].name == "来自全局"
    assert skills[0].source == global_dir / "dup" / "SKILL.md"
