"""frontmatter 解析单测；Phase 5 工具描述 + Phase 6 SKILL.md 共用。"""

from __future__ import annotations

import pytest

from sanshiliu.foundation.frontmatter import parse


def test_no_frontmatter() -> None:
    p = parse("just body text")
    assert p.frontmatter == {}
    assert p.body == "just body text"


def test_basic_frontmatter() -> None:
    p = parse("---\nname: foo\ndesc: bar\n---\nbody")
    assert p.frontmatter == {"name": "foo", "desc": "bar"}
    assert p.body == "body"


def test_nested_frontmatter() -> None:
    text = """---
name: t
parameters:
  type: object
  required: [q]
---
hello"""
    p = parse(text)
    assert p.frontmatter["name"] == "t"
    assert p.frontmatter["parameters"]["required"] == ["q"]


def test_unclosed_frontmatter_treated_as_body() -> None:
    p = parse("---\nfoo: bar\nno-close-here")
    assert p.frontmatter == {}


def test_invalid_yaml_raises() -> None:
    with pytest.raises(ValueError):
        parse("---\n: bad : yaml :\n---\nbody")


def test_non_dict_frontmatter_raises() -> None:
    with pytest.raises(ValueError):
        parse("---\n- just\n- a\n- list\n---\nbody")
