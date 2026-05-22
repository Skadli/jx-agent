"""Phase 6 skills 单测；loader 优先级 + matcher + activator + Claude SKILL.md 兼容。"""

from __future__ import annotations

from pathlib import Path

from sanshiliu.skills.activator import SkillActivator
from sanshiliu.skills.loader import SkillLoader
from sanshiliu.skills.matcher import KeywordMatcher, SemanticMatcher
from sanshiliu.skills.types import SkillDef


def _write_skill(root: Path, sid: str, name: str, desc: str, keywords: list[str], body: str = "skill body") -> None:
    d = root / sid
    d.mkdir(parents=True, exist_ok=True)
    kw_yaml = "\n".join(f"  - {k}" for k in keywords)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\nkeywords:\n{kw_yaml}\n---\n{body}",
        encoding="utf-8",
    )


def test_loader_scans_and_parses(tmp_path: Path) -> None:
    repo = tmp_path / "skills"
    _write_skill(repo, "alpha", "Alpha", "Alpha 技能", ["aa"])
    _write_skill(repo, "beta", "Beta", "Beta 技能", ["bb"])
    loader = SkillLoader([repo])
    skills = loader.load()
    assert len(skills) == 2
    assert {s.id for s in skills} == {"alpha", "beta"}


def test_loader_priority_project_over_repo(tmp_path: Path) -> None:
    """V-5：同名 skill 项目级覆盖仓库内。"""
    project = tmp_path / "proj_skills"
    repo = tmp_path / "skills"
    _write_skill(project, "x", "ProjectX", "from project", ["px"])
    _write_skill(repo, "x", "RepoX", "from repo", ["rx"])
    loader = SkillLoader([project, repo])
    skills = loader.load()
    assert len(skills) == 1
    assert skills[0].name == "ProjectX"
    assert skills[0].priority == 0  # 高优先级 = 索引小


def test_loader_skips_missing_required_fields(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "broken"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: broken\n---\nno description", encoding="utf-8")
    loader = SkillLoader([tmp_path / "skills"])
    skills = loader.load()
    assert skills == []


def test_loader_handles_dir_without_skill_md(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "no-md"
    d.mkdir(parents=True)
    (d / "README.md").write_text("not a skill", encoding="utf-8")
    loader = SkillLoader([tmp_path / "skills"])
    assert loader.load() == []


def test_keyword_matcher_hits() -> None:
    s = SkillDef(id="vid", name="Video", description="d", keywords=["剪映", "premiere"])
    m = KeywordMatcher()
    assert m.match(s, "我用剪映剪了个视频") is True
    assert m.match(s, "用 Premiere 怎么调色") is True
    assert m.match(s, "今天吃啥") is False


def test_keyword_matcher_case_insensitive() -> None:
    s = SkillDef(id="t", name="t", description="d", keywords=["DaVinci"])
    m = KeywordMatcher()
    assert m.match(s, "我在用 davinci resolve") is True


def test_semantic_matcher_disabled_returns_false() -> None:
    s = SkillDef(id="t", name="t", description="d", keywords=[])
    sm = SemanticMatcher(embedding_fn=None)
    assert sm.match(s, "any text") is False


def test_activator_filters_and_assembles(tmp_path: Path) -> None:
    """V-2：用户问含关键词 → skill 被加载到 prompt。"""
    repo = tmp_path / "skills"
    _write_skill(repo, "vid", "Video", "video desc", ["剪映"], body="# Video 技能正文")
    _write_skill(repo, "other", "Other", "other desc", ["xyz"], body="# Other 正文")
    loader = SkillLoader([repo])
    loader.load()
    act = SkillActivator(loader)
    actives = act.activate_for("我想学剪映")
    assert len(actives) == 1
    assert actives[0].id == "vid"
    prompt = act.to_prompt_addition(actives)
    assert "# Video 技能正文" in prompt
    # 未命中 skill 不应出现
    assert "Other 正文" not in prompt


def test_activator_no_match_returns_empty_prompt(tmp_path: Path) -> None:
    repo = tmp_path / "skills"
    _write_skill(repo, "vid", "Video", "d", ["剪映"])
    loader = SkillLoader([repo])
    loader.load()
    act = SkillActivator(loader)
    assert act.activate_for("无关问题") == []
    assert act.to_prompt_addition([]) == ""


def test_loader_claude_compat_frontmatter(tmp_path: Path) -> None:
    """V-4：兼容 Claude SKILL.md frontmatter（name/description/keywords）。"""
    d = tmp_path / "claude_compat"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: claude-style\ndescription: Claude 原生格式\nkeywords: [foo, bar, baz]\n---\n# Body",
        encoding="utf-8",
    )
    loader = SkillLoader([tmp_path])
    skills = loader.load()
    assert len(skills) == 1
    assert skills[0].keywords == ["foo", "bar", "baz"]
