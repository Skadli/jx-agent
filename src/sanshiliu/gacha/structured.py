"""LLM 结构化输出解析。

两种形态：
- `parse_sectioned_output`：forge 章产物的**首选**——大段正文用 `===标记===` 裸文本承载、
  只有 META 是一小段 JSON，根除"上万 token 自由文本塞进 JSON 字符串"的转义崩坏
  （详见该函数 docstring）。
- `parse_structured_output` / `parse_json_object`：纯 JSON 提取，容错三连
  （fenced 块 → 整段 → 首尾花括号截取）。rarity（评级小 JSON）、skill_autoinstall
  （clawhub inspect --json）、以及 forge 对老式 JSON 输出的回退共用。

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


# ── 分段输出（forge 章产物专用）──
# 标记整行独占，形如 ===NARRATIVE=== / ===PERSONA:IDENTITY=== / ===META=== / ===END===。
# 名字限纯大写 ASCII（+ : _ 数字），故中文标题、markdown 的裸 `===`（setext H1）都不会误命中。
_SECTION_HEAD_RE = re.compile(r"(?m)^[ \t]*={3,}[ \t]*([A-Z][A-Z0-9_:]*)[ \t]*={3,}[ \t]*$")

# PERSONA:* 标记 → card_persona.PERSONA_SECTION_FILES 的键（此处不 import 那边，避免反向依赖；
# 两处键名须一致，改一边记得改另一边）。
_PERSONA_SECTION_MARKERS: dict[str, str] = {
    "PERSONA:IDENTITY": "identity",
    "PERSONA:PERSONALITY": "personality",
    "PERSONA:BELIEFS": "beliefs",
    "PERSONA:STYLE": "style",
    "PERSONA:FEWSHOT": "fewshot_short",
}


def parse_sectioned_output(raw: str) -> dict[str, Any] | None:
    """解析 forge 章产物的**分段格式**：大段正文裸文本承载、只有 META 是小 JSON。

    这是章产物的首选格式（forge_runner 用），取代"整章塞进一个 JSON 字符串"——后者要求模型
    在上万 token 自由文本里零转义错误（漏一个未转义的 " 就提前截断字符串、LaTeX/路径里的 \\
    触发 Invalid \\escape），长文本下几乎必崩，且"修复重发"会再产同样的多行字符串二次崩。
    分段后大段正文永不进 JSON、无需转义，转义类崩溃整类消失；仅 META（age_range / learned /
    skill_intents / personality 一句话 + 首章 origin / trigger / talents）是一小段低风险 JSON，
    且即使 META 写坏也只丢这几个可回落字段，不否定整章。

    产出与旧 JSON 路径**同形状**的 dict（narrative / report / persona{...} / age_range /
    learned / skill_intents / personality / ...），故下游 _coerce_chapter_payload 等全不用改。
    无任何分隔符标记 → 返回 None（调用方据此回退老式 JSON 解析或走一次修复重发）。
    """
    if not raw or not raw.strip():
        return None
    heads = list(_SECTION_HEAD_RE.finditer(raw))
    if not heads:
        return None

    # 每个标记到下一个标记（或文末）之间是该段正文；同名标记重复则后者覆盖（容忍模型写两遍 META）。
    sections: dict[str, str] = {}
    for i, head in enumerate(heads):
        body_end = heads[i + 1].start() if i + 1 < len(heads) else len(raw)
        sections[head.group(1)] = raw[head.end() : body_end].strip("\n")

    out: dict[str, Any] = {}
    narrative = sections.get("NARRATIVE", "").strip()
    if narrative:
        out["narrative"] = narrative
    report = sections.get("REPORT", "").strip()
    if report:
        out["report"] = report
    persona: dict[str, str] = {}
    for marker, key in _PERSONA_SECTION_MARKERS.items():
        body = sections.get(marker, "").strip()
        if body:
            persona[key] = body
    if persona:
        out["persona"] = persona
    meta_raw = sections.get("META", "").strip()
    if meta_raw:
        # META 走完整容错（可能被模型裹了 ```json``` 围栏）；并进来但不覆盖裸文本段拿到的字段。
        meta = parse_structured_output(meta_raw)
        if meta is not None:
            for k, v in meta.items():
                out.setdefault(k, v)
    return out or None


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
