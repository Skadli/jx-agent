"""YAML frontmatter 解析；tool 描述、SKILL.md、memdir frontmatter 共用此实现。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

# YAML frontmatter 分隔符；与 Jekyll / Hugo / Claude 约定一致
_DELIMITER = "---"


@dataclass(frozen=True)
class ParsedMarkup:
    """frontmatter 字典 + 正文字符串；二者均可为空。"""

    frontmatter: dict[str, Any]
    body: str
    raw: str


def parse(text: str) -> ParsedMarkup:
    """解析 markdown：开头若为 ``---`` 则取至下一个 ``---`` 之间作 YAML；否则 frontmatter 为空。"""
    lines = text.splitlines(keepends=False)
    if not lines or lines[0].strip() != _DELIMITER:
        return ParsedMarkup(frontmatter={}, body=text.strip(), raw=text)

    # 找闭合 ---
    end_idx = -1
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == _DELIMITER:
            end_idx = i
            break
    if end_idx < 0:
        # 开头有 --- 但没闭合；当作无 frontmatter
        return ParsedMarkup(frontmatter={}, body=text.strip(), raw=text)

    fm_text = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1 :]).strip()
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"frontmatter YAML 解析失败：{exc}") from exc
    if not isinstance(fm, dict):
        raise ValueError(f"frontmatter 必须是字典，实际：{type(fm).__name__}")
    return ParsedMarkup(frontmatter=fm, body=body, raw=text)
