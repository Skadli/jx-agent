"""Dashboard skill structure reader.

Each skill owns a curated ``skills/<skill-id>/structure.json`` file. The
dashboard API reads that file directly instead of deriving a graph from
``SKILL.md`` at runtime.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from sanshiliu.skills.types import SkillDef

_STRUCTURE_FILENAME = "structure.json"


def skill_structure_path(skill: SkillDef) -> Path:
    """Return the dashboard structure file path for a skill."""
    return skill.source.parent / _STRUCTURE_FILENAME


def read_skill_structure(skill: SkillDef) -> dict[str, Any]:
    """Read ``skills/<id>/structure.json`` and validate its minimal schema."""
    path = skill_structure_path(skill)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _validate_structure_payload(payload, path)


def _validate_structure_payload(payload: Any, path: Path) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"{path} 不是 JSON object")
    data = cast(dict[str, Any], payload)
    if not isinstance(data.get("nodes"), list):
        raise ValueError(f"{path} 缺少 nodes 数组")
    if not isinstance(data.get("edges"), list):
        raise ValueError(f"{path} 缺少 edges 数组")
    if not isinstance(data.get("meta"), dict):
        raise ValueError(f"{path} 缺少 meta 对象")
    return data
