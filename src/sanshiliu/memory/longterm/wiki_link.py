"""wiki-link 解析；解析 [[name]] / [[name|alias]] 但 1.0 不利用拓扑。"""

from __future__ import annotations

import re

# Claude 协议一致：[[<name>]] 或 [[<name>|<alias>]]
_WIKI_LINK_RE = re.compile(r"\[\[([^\[\]|]+)(?:\|([^\[\]]+))?\]\]")


def parse_links(text: str) -> list[tuple[str, str | None]]:
    """返回 (name, alias) 列表；alias 缺省时为 None。"""
    out: list[tuple[str, str | None]] = []
    for m in _WIKI_LINK_RE.finditer(text):
        name = m.group(1).strip()
        alias = m.group(2).strip() if m.group(2) else None
        if name:
            out.append((name, alias))
    return out


def strip_links(text: str) -> str:
    """把 [[name|alias]] 替换为 alias（无则 name）；用于摘要等不需要拓扑的场景。"""
    def _sub(m: re.Match[str]) -> str:
        return (m.group(2) or m.group(1) or "").strip()
    return _WIKI_LINK_RE.sub(_sub, text)
