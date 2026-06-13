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


# ────────── LLM 生成 structure.json（纯逻辑；LLM 调用在 web 层 L9 编排） ──────────

# 节点 data.type / 边 data.kind 的合法集合（与 skill_canvas.jsx 的 NODE_THEMES / EDGE_STYLES 对齐）
NODE_TYPES = ("trigger", "step", "tool", "subagent", "resource", "output")
EDGE_KINDS = ("sequence", "anchor", "tool", "subagent", "resource")
# 主链类型（trigger→step…→output）走横向网格；其余作分支挂在下方泳道
_MAIN_NODE_TYPES = frozenset({"trigger", "step", "output"})
_COL_WIDTH = 300  # 列距：x 按序号 * COL_WIDTH
_MAIN_LANE_Y = 260  # 主链泳道 y
_BRANCH_LANE_Y = 430  # 分支泳道 y（前端 relayoutByLanes 再消重叠）


def build_structure_prompt(skill: SkillDef) -> str:
    """拼"把 SKILL.md 转成画布图"的 LLM prompt：要求只产 {nodes, edges}（meta 由后端确定性填）。

    节点/边 schema、坐标网格约定、输出纪律全部内联——这是零工具的一次性结构化生成。
    """
    node_types = "、".join(NODE_TYPES)
    edge_kinds = "、".join(EDGE_KINDS)
    return (
        "你是工作流图建模助手。下面是一个 Claude 协议 skill 的 SKILL.md 正文，"
        "请把它的执行流程抽象成一张有向图（节点 + 边），用于在只读画布上可视化这个 skill 怎么干活。\n\n"
        f"=== SKILL: {skill.id}（{skill.name}）===\n"
        f"描述：{skill.description}\n"
        f"触发词：{'、'.join(skill.keywords) if skill.keywords else '（无）'}\n\n"
        "=== SKILL.md 正文 ===\n"
        f"{skill.body.strip()}\n"
        "=== 正文结束 ===\n\n"
        "请输出**且仅输出**一个 JSON 对象，形如 {\"nodes\": [...], \"edges\": [...]}，"
        "不要 markdown 代码围栏、不要任何解释文字。\n\n"
        "节点 node 形状（每个字段都要给，**不要写坐标 position，布局由系统自动排版**）：\n"
        '  {"id": "短横线小写英文唯一 id", "type": "custom", '
        '"data": {"type": "节点类型", "title": "短标题", "desc": "一行关键词副标题", "raw": "1-2 句详情"}}\n'
        f"  data.type 只能取：{node_types}。\n"
        "  - trigger：何时/为何激活这个 skill（**有且仅有一个**，放在数组最前）；\n"
        "  - step：工作流中的一个步骤（主体，**按执行顺序排在数组里**，顺序即左→右布局）；\n"
        "  - output：最终产物 / 收尾停止条件（通常一个，排在主链最后）；\n"
        "  - resource：skill 会读取的参考文件（如 references/*.md）；\n"
        "  - subagent：skill 会派生的子 agent（如 agents/*.md）；\n"
        "  - tool：skill 会调用的工具 / 脚本。\n"
        "  resource/subagent/tool 仅在 SKILL.md 真的提到时才加，用边挂到触发它的那个 step 上。\n\n"
        "边 edge 形状：\n"
        '  {"id": "e-源-目标", "source": "源节点id", "target": "目标节点id", '
        '"type": "custom", "data": {"kind": "边类型"}}\n'
        f"  data.kind 只能取：{edge_kinds}。主链 trigger→首 step、末 step→output 用 anchor；"
        "step→step 用 sequence；step→resource/subagent/tool 分别用 resource/subagent/tool。\n"
        "  每条边的 source/target 都**必须**指向上面真实存在的节点 id，不许悬空。\n\n"
        "规模与语言：总节点数控制在 6–14 个，抓主干、别把每句话都拆成节点；"
        "title/desc/raw **一律写中文**——即使 SKILL.md 正文是英文，也要把它转述/翻译成中文，"
        "只有专有名词、工具名、文件名（如 references/foo.md）可保留英文原文；"
        "title 尽量短（≤16 字），desc 是几个关键词，raw 是一两句话。"
        "（节点 id 仍用小写英文短横线，不影响展示。）"
    )


def coerce_structure(parsed: dict[str, Any], skill: SkillDef) -> dict[str, Any]:
    """把 LLM 产出的 {nodes, edges} 规整 + 校验成可安全落盘的完整 structure（含确定性 meta）。

    - 节点：丢弃缺 id / 重复 id / 非 dict 的；type 越界降级为 step；坐标后端确定性指派；统一 node.type="custom"。
    - 边：丢弃端点不存在的悬空边；kind 越界降级为 sequence；缺 id 自动合成；去重。
    - meta：skill 信息 / 计数 / raw_body 全由后端确定性填（不信 LLM），并标 generated_by=llm。
    无任何有效节点 → raise ValueError（调用方据此返回错误，不落盘垃圾）。
    """
    raw_nodes = parsed.get("nodes")
    if not isinstance(raw_nodes, list):
        raise ValueError("缺少 nodes 数组")
    edges_obj = parsed.get("edges")
    raw_edges = edges_obj if isinstance(edges_obj, list) else []

    warnings: list[str] = []
    nodes: list[dict[str, Any]] = []
    ids: set[str] = set()
    bad_type = 0
    main_col = 0
    branch_col = 0

    for raw in raw_nodes:
        if not isinstance(raw, dict):
            continue
        nid = raw.get("id")
        if not isinstance(nid, str) or not nid.strip() or nid in ids:
            continue
        nid = nid.strip()
        data_obj = raw.get("data")
        data = data_obj if isinstance(data_obj, dict) else {}
        ntype = data.get("type")
        if ntype not in NODE_TYPES:
            ntype = "step"
            bad_type += 1
        title_obj = data.get("title")
        title = title_obj.strip() if isinstance(title_obj, str) and title_obj.strip() else nid
        desc_obj = data.get("desc")
        desc = desc_obj if isinstance(desc_obj, str) else ""
        raw_obj = data.get("raw")
        rawtext = raw_obj if isinstance(raw_obj, str) else ""

        # 坐标一律后端确定性指派（不信 LLM 坐标——它常把点堆在 0,0 或互相重叠；prompt 也已不让它写坐标）：
        # 数组顺序即布局序。主链(trigger/step/output)排一行 y=260，分支(resource/subagent/tool)排下方
        # 一行 y=430，各自按出现序占独立列 x=col*300，保证无两点同坐标；前端 relayoutByLanes/fitView 再微调。
        if ntype in _MAIN_NODE_TYPES:
            position = {"x": main_col * _COL_WIDTH, "y": _MAIN_LANE_Y}
            main_col += 1
        else:
            position = {"x": branch_col * _COL_WIDTH, "y": _BRANCH_LANE_Y}
            branch_col += 1

        ids.add(nid)
        nodes.append({
            "id": nid,
            "type": "custom",
            "position": position,
            "data": {"type": ntype, "title": title, "desc": desc, "raw": rawtext},
        })

    if not nodes:
        raise ValueError("生成结果没有有效节点")

    edges: list[dict[str, Any]] = []
    edge_ids: set[str] = set()
    dropped_edges = 0
    bad_kind = 0
    for raw in raw_edges:
        if not isinstance(raw, dict):
            continue
        src = raw.get("source")
        tgt = raw.get("target")
        if src not in ids or tgt not in ids:
            dropped_edges += 1
            continue
        edata = raw.get("data")
        kind = edata.get("kind") if isinstance(edata, dict) else None
        if kind not in EDGE_KINDS:
            kind = "sequence"
            bad_kind += 1
        eid = raw.get("id")
        if not isinstance(eid, str) or not eid.strip() or eid in edge_ids:
            eid = f"e-{src}-{tgt}"
        base_eid = eid
        suffix = 2
        while eid in edge_ids:  # 同源同目标多条边时 id 去重
            eid = f"{base_eid}-{suffix}"
            suffix += 1
        edge_ids.add(eid)
        edges.append({
            "id": eid,
            "source": src,
            "target": tgt,
            "type": "custom",
            "data": {"kind": kind},
        })

    if dropped_edges:
        warnings.append(f"丢弃 {dropped_edges} 条悬空边（端点不存在）")
    if bad_type:
        warnings.append(f"{bad_type} 个节点类型非法，已降级为 step")
    if bad_kind:
        warnings.append(f"{bad_kind} 条边类型非法，已降级为 sequence")

    counts = {t: 0 for t in NODE_TYPES}
    for n in nodes:
        counts[n["data"]["type"]] += 1

    meta = {
        "structure_version": 2,
        "skill_id": skill.id,
        "skill_name": skill.name,
        "description": skill.description,
        "keywords": list(skill.keywords),
        "source": str(skill.source),
        "structure_file": str(skill_structure_path(skill)),
        "generated_by": "llm",  # 机器生成标记：区别于手工 curated 结构，提示可能需复核
        "raw_body": skill.body,
        "has_workflow": counts["step"] > 0,
        "warnings": warnings,
        "step_count": counts["step"],
        "tool_count": counts["tool"],
        "subagent_count": counts["subagent"],
        "resource_count": counts["resource"],
    }
    return {"nodes": nodes, "edges": edges, "meta": meta}


def write_skill_structure(skill: SkillDef, data: dict[str, Any]) -> Path:
    """把 structure 落盘到 skills/<id>/structure.json（独占创建，**永不覆盖**；2 空格缩进，UTF-8，保留中文）。

    用 "x" 独占创建：文件已存在则 raise FileExistsError——这是"永不覆盖"契约的原子收口，挡住
    handler 里 path.exists() 预检到真正写入之间长达 ~180s 的 LLM 窗口期内并发冒出的写/已有文件。
    """
    path = skill_structure_path(skill)
    with path.open("x", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return path
