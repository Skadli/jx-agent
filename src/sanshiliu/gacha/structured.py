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
    # strict=False：允许字符串值里出现未转义的控制字符（\n \t \r）。LLM 把整段 markdown 传记
    # 塞进 narrative 这个 JSON 字符串时几乎总带真实换行/制表符，strict=True（默认）会以
    # "Invalid control character" 直接拒掉——这正是"锻造 JSON 无法解析→整章失败→抽卡经常失败"
    # 的主因（连修复重发也产同样的多行字符串，二次失败）。放宽控制字符即可吃下模型的自然产物。
    try:
        obj = json.loads(text, strict=False)
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
