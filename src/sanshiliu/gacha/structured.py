"""LLM 结构化输出 JSON 提取：容错三连（fenced 块 → 整段 → 首尾花括号截取）。

forge_runner（章产物）、rarity（评级）、skill_autoinstall（clawhub inspect --json）共用。
逻辑平移自 scheduler/growth_runner（老链路冻结待退役，这里是新链路的唯一一份）。
"""

from __future__ import annotations

import json
import re
from typing import Any

# 从 LLM 输出里抠 JSON：优先 ```json fenced``` 块，其次裸 {...}
_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def try_json(text: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def parse_structured_output(raw: str) -> dict[str, Any] | None:
    """从 LLM 文本里解析结构化 JSON 对象；失败返 None（调用方据此修复重试或硬失败）。

    容错顺序：① ```json fenced``` 块；② 整段当 JSON；③ 第一个 { 到最后一个 } 截取。
    解析出非 dict 也算失败。
    """
    if not raw or not raw.strip():
        return None

    m = _FENCE_RE.search(raw)
    if m is not None:
        obj = try_json(m.group(1))
        if obj is not None:
            return obj

    obj = try_json(raw.strip())
    if obj is not None:
        return obj

    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        obj = try_json(raw[start : end + 1])
        if obj is not None:
            return obj
    return None


def parse_json_object(raw: str) -> dict[str, Any] | None:
    """宽松解析一段"应当是 JSON 对象"的命令行输出（如 clawhub inspect --json）。"""
    obj = try_json(raw.strip())
    if obj is not None:
        return obj
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        return try_json(raw[start : end + 1])
    return None
