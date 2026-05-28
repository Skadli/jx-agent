"""SKILL.md → 节点图启发式解析；L6 Skills 层；纯函数，无副作用。

输出形如 {"nodes": [...], "edges": [...], "meta": {...}}，给 dashboard 画布消费。
节点类型 trigger / step / tool / subagent / resource / output 共 6 类
（resource 是 subagent 的同形态变体：引用的子文件 = resource，引用的 agents/*.md = subagent）。

边规则参考 research/skill-viz-patterns.md：相邻 step 顺序边、tool 短挂边、subagent 虚线、
resource 灰双向、trigger→首 step、末 step→output 共 6 类。

不引入新依赖：用 stdlib re + 已有 frontmatter parser。
"""

from __future__ import annotations

import re
from typing import Any

from sanshiliu.skills.types import SkillDef

# 已知 tool 名集合 —— 同时收纳 jx-agent runtime 名（bash_exec 等）和 Claude protocol 名
# （Bash 等），SKILL.md 作者两种风格都可能用。新增 tool 时同步追加这里。
_KNOWN_TOOLS: frozenset[str] = frozenset({
    # jx-agent runtime
    "bash_exec", "file_read", "file_write", "web_search",
    "load_persona_module", "memory_load", "memory_save", "skill",
    # Claude protocol (大小写敏感，正则 \b 匹配)
    "Bash", "Read", "Write", "Edit", "Glob", "Grep",
    "WebSearch", "WebFetch", "Skill", "SkillTool",
    "LoadPersonaModule", "LoadMemory", "SaveMemory",
    "TodoWrite", "AskUserQuestion", "NotebookEdit",
})

# 启发式 heading 词典 —— 中英文都收，case-insensitive
_TRIGGER_HEADINGS = ("when to use", "activation signals", "when to trigger",
                     "触发", "何时使用", "触发前的就绪检查")
_WORKFLOW_HEADINGS = ("workflow", "steps", "your task", "core rules",
                      "六步协议", "协议", "工作流", "步骤")
_OUTPUT_HEADINGS = ("output", "recommendation format", "report structure",
                    "输出", "输出格式", "醒来")

# Dify 视觉常量（见 research/dify-canvas-stack.md）
_NODE_WIDTH = 240
_X_OFFSET = 60          # 节点间水平间距，col 总距 = NODE_WIDTH + X_OFFSET = 300
_Y_OFFSET = 39          # 兜底垂直间距
_LAYER_X = _NODE_WIDTH + _X_OFFSET   # 300
_BRANCH_Y = 130         # tool / subagent / resource 挂在 step 上下时的 y 偏移
_BASELINE_Y = 280       # trigger / step / output 主轴的 y 基线
_MAX_STEPS_PER_ROW = 5  # step 超过这个数自动换行，避免水平拖到天涯
_ROW_HEIGHT = 380       # 行间距（容纳 tool/subagent 挂在上/下后的高度）


def parse_skill_structure(skill: SkillDef) -> dict[str, Any]:
    """主入口；返回 {nodes, edges, meta}。失败时也尽量返回部分结果 + meta.warnings。"""
    warnings: list[str] = []
    sections = _split_sections(skill.body)

    # 1. trigger 节点（必有兜底）
    trigger_node = _build_trigger(skill, sections)

    # 2. step 节点序列
    step_nodes = _build_steps(sections)
    if not step_nodes:
        warnings.append("未检测到 ## Workflow / ## Steps 等工作流结构；画布将只显示输入/主体/输出三节点")
        step_nodes = [_make_node(
            node_id="body",
            sub_type="step",
            title=skill.name,
            desc=skill.description[:80],
            raw=skill.body[:600],
            x=_LAYER_X,
            y=_BASELINE_Y,
        )]

    # 重排 step 位置（横向；超过 _MAX_STEPS_PER_ROW 自动换行成蛇形 / boustrophedon —
    # 偶数行从左到右，奇数行从右到左，避免行末到下一行行首拉一条长对角线）
    for idx, n in enumerate(step_nodes):
        row, col = divmod(idx, _MAX_STEPS_PER_ROW)
        x_col = col if row % 2 == 0 else (_MAX_STEPS_PER_ROW - 1 - col)
        n["position"] = {
            "x": _LAYER_X * (x_col + 1),
            "y": _BASELINE_Y + row * _ROW_HEIGHT,
        }

    # 3. tool / subagent / resource 节点 —— 挂在 step 旁边
    branch_nodes: list[dict[str, Any]] = []
    branch_edges: list[dict[str, Any]] = []
    skill_dir = skill.source.parent
    seen_branch_ids: set[str] = set()

    # frontmatter 声明的 allowed-tools / tools 也算 tool 信号，挂到第一个 step
    fm_tools = _extract_frontmatter_tools(skill)
    for slot, tool_name in enumerate(fm_tools):
        node_id = f"tool-{tool_name}"
        if node_id in seen_branch_ids:
            continue
        branch_nodes.append(_make_node(
            node_id=node_id,
            sub_type="tool",
            title=tool_name,
            desc="allowed-tools 声明",
            raw=tool_name,
            x=step_nodes[0]["position"]["x"] if step_nodes else _LAYER_X,
            y=_BASELINE_Y - _BRANCH_Y - slot * 90,
        ))
        seen_branch_ids.add(node_id)
        if step_nodes:
            branch_edges.append(_make_edge(step_nodes[0]["id"], node_id, "tool"))

    for step in step_nodes:
        raw_text = step["data"]["raw"]
        x_base = step["position"]["x"]

        # tool refs
        for slot, tool_name in enumerate(_extract_tool_refs(raw_text)):
            node_id = f"tool-{tool_name}"
            if node_id not in seen_branch_ids:
                branch_nodes.append(_make_node(
                    node_id=node_id,
                    sub_type="tool",
                    title=tool_name,
                    desc="工具调用",
                    raw=tool_name,
                    x=x_base,
                    y=_BASELINE_Y - _BRANCH_Y - slot * 90,
                ))
                seen_branch_ids.add(node_id)
            branch_edges.append(_make_edge(step["id"], node_id, "tool"))

        # subagent refs（引用 agents/X.md）
        for slot, agent_file in enumerate(_extract_subagent_refs(raw_text, skill_dir)):
            node_id = f"subagent-{agent_file}"
            if node_id not in seen_branch_ids:
                branch_nodes.append(_make_node(
                    node_id=node_id,
                    sub_type="subagent",
                    title=agent_file,
                    desc="子 agent",
                    raw=agent_file,
                    x=x_base,
                    y=_BASELINE_Y + _BRANCH_Y + slot * 90,
                ))
                seen_branch_ids.add(node_id)
            branch_edges.append(_make_edge(step["id"], node_id, "subagent"))

        # resource refs（引用 references/scripts/assets/ 子文件）
        for slot, res_file in enumerate(_extract_resource_refs(raw_text, skill_dir)):
            node_id = f"resource-{res_file}"
            if node_id not in seen_branch_ids:
                branch_nodes.append(_make_node(
                    node_id=node_id,
                    sub_type="resource",
                    title=res_file,
                    desc="子文件资源",
                    raw=res_file,
                    x=x_base,
                    y=_BASELINE_Y + _BRANCH_Y * 2 + slot * 90,
                ))
                seen_branch_ids.add(node_id)
            branch_edges.append(_make_edge(step["id"], node_id, "resource"))

    # 4. output 节点 —— 跟着 step 序列的最后一个所在行
    output_node = _build_output(sections, warnings)
    if step_nodes:
        last_pos = step_nodes[-1]["position"]
        # 同行下一列；如果当前是该行最右一列，则换行
        last_idx = len(step_nodes) - 1
        last_row = last_idx // _MAX_STEPS_PER_ROW
        last_col_in_row = last_idx % _MAX_STEPS_PER_ROW
        if last_col_in_row >= _MAX_STEPS_PER_ROW - 1:
            output_node["position"] = {"x": _LAYER_X, "y": last_pos["y"] + _ROW_HEIGHT}
        else:
            # 蛇形：判断该行方向，决定向左还是向右
            direction = 1 if last_row % 2 == 0 else -1
            output_node["position"] = {"x": last_pos["x"] + _LAYER_X * direction, "y": last_pos["y"]}
    else:
        output_node["position"] = {"x": _LAYER_X * 2, "y": _BASELINE_Y}

    # 5. 顺序边 + 锚点边
    seq_edges = [
        _make_edge(step_nodes[i]["id"], step_nodes[i + 1]["id"], "sequence")
        for i in range(len(step_nodes) - 1)
    ]
    anchor_edges = [
        _make_edge(trigger_node["id"], step_nodes[0]["id"], "anchor"),
        _make_edge(step_nodes[-1]["id"], output_node["id"], "anchor"),
    ]

    nodes = [trigger_node, *step_nodes, *branch_nodes, output_node]
    edges = [*seq_edges, *branch_edges, *anchor_edges]

    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "skill_id":     skill.id,
            "skill_name":   skill.name,
            "description":  skill.description,
            "keywords":     skill.keywords,
            "source":       str(skill.source),
            "raw_body":     skill.body,          # 给前端「源码」tab 用，省一次请求
            "step_count":   len(step_nodes),
            "tool_count":   sum(1 for n in branch_nodes if n["data"]["type"] == "tool"),
            "subagent_count": sum(1 for n in branch_nodes if n["data"]["type"] == "subagent"),
            "resource_count": sum(1 for n in branch_nodes if n["data"]["type"] == "resource"),
            "has_workflow": bool(step_nodes and step_nodes[0]["id"] != "body"),
            "warnings":     warnings,
        },
    }


# ────────── 节点 / 边构造 ──────────

def _make_node(
    *, node_id: str, sub_type: str, title: str, desc: str,
    raw: str, x: int, y: int,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "custom",          # 统一 custom（Dify 关键技巧），按 data.type 内部分流
        "position": {"x": x, "y": y},
        "data": {
            "type":  sub_type,     # trigger / step / tool / subagent / resource / output
            "title": title,
            "desc":  desc,
            "raw":   raw,
        },
    }


def _make_edge(source: str, target: str, kind: str) -> dict[str, Any]:
    return {
        "id":     f"e-{source}-{target}",
        "source": source,
        "target": target,
        "type":   "custom",
        "data":   {"kind": kind},   # sequence / tool / subagent / resource / anchor
    }


# ────────── markdown 切段 ──────────

# H2 / H3 行（不允许行首缩进；ATX 风格）；保留井号数让上层判等级
_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.MULTILINE)


def _split_sections(body: str) -> list[dict[str, Any]]:
    """按 H2 切段（H3 留在段内）；返回 [{title, level, content}]。"""
    matches = list(_HEADING_RE.finditer(body))
    if not matches:
        return [{"title": "", "level": 0, "content": body}]

    sections: list[dict[str, Any]] = []
    # 处理首段（第一个 H2 之前的内容）当作 intro，title 空
    first_h2_pos = next((m.start() for m in matches if len(m.group(1)) == 2), None)
    if first_h2_pos and first_h2_pos > 0:
        intro = body[:first_h2_pos].strip()
        if intro:
            sections.append({"title": "", "level": 0, "content": intro})

    # 只按 H2 切（H3 留在所属 H2 段内）
    h2_matches = [m for m in matches if len(m.group(1)) == 2]
    for i, m in enumerate(h2_matches):
        start = m.end()
        end = h2_matches[i + 1].start() if i + 1 < len(h2_matches) else len(body)
        sections.append({
            "title":   m.group(2).strip(),
            "level":   2,
            "content": body[start:end].strip(),
        })
    return sections


def _is_heading_match(title: str, keywords: tuple[str, ...]) -> bool:
    """case-insensitive substring 匹配；标题里包含任一关键词即算。"""
    lt = title.lower()
    return any(k in lt for k in keywords)


# ────────── trigger 节点 ──────────

def _build_trigger(skill: SkillDef, sections: list[dict[str, Any]]) -> dict[str, Any]:
    """trigger 信号优先级：frontmatter.keywords > Activation Signals > When to Use > description 尾句。"""
    parts: list[str] = []
    if skill.keywords:
        parts.append("关键词：" + "、".join(skill.keywords[:8]))
    for sec in sections:
        if _is_heading_match(sec["title"], _TRIGGER_HEADINGS):
            parts.append(sec["content"][:300])
            break
    if not parts and skill.description:
        parts.append(skill.description[:200])

    return _make_node(
        node_id="trigger",
        sub_type="trigger",
        title="输入触发",
        desc=skill.keywords[0] if skill.keywords else "用户请求",
        raw="\n\n".join(parts),
        x=0,
        y=_BASELINE_Y,
    )


# ────────── step 节点 ──────────

# 段内有序列表项：行首 N. 或 N)（允许行首缩进；只取顶层列表，不递归子项）
_NUMBERED_LIST_RE = re.compile(r"^(?:\d+[.)])\s+(.+?)(?=\n(?:\d+[.)])\s+|\n#{2,3}\s+|\Z)",
                                re.MULTILINE | re.DOTALL)
# H3 风格的编号小标题：### 第 1 步 / ### Step 1 / ### 1. xxx
_NUMBERED_H3_RE = re.compile(
    r"^###\s+(?:Step\s+\d+|第\s*\d+\s*步|\d+[.)])\s*[·:：—\-]?\s*(.*?)\s*$",
    re.MULTILINE,
)


def _build_steps(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """优先抓 ## Workflow / ## Steps 下的有序列表 或 ### 第 N 步 风格 H3。

    第一个命中的 workflow 段即返回，不累积多段（否则像 skill-finder 同时有 Core Rules
    与 Workflow 时会把 step 重复一遍）。
    """
    candidates: list[tuple[str, str]] = []   # (title, raw_block)

    for sec in sections:
        if not _is_heading_match(sec["title"], _WORKFLOW_HEADINGS):
            continue
        content = sec["content"]

        # 优先级 A：有序 H3 编号小标题（dream 的「第 N 步」风格）
        h3_blocks = _split_by_h3(content)
        h3_step_titles: list[tuple[str, str]] = []
        for h3_match, blk in h3_blocks:
            numbered_match = _NUMBERED_H3_RE.match(h3_match.group(0))
            if numbered_match is None:
                continue
            title_part = numbered_match.group(1).strip() or sec["title"]
            h3_step_titles.append((title_part, _strip_numbered(blk)))
        if h3_step_titles:
            candidates.extend(h3_step_titles)
            break

        # 优先级 B：段内顶层有序列表
        for m in _NUMBERED_LIST_RE.finditer(content):
            item_text = m.group(1).strip()
            first_line, _, _rest = item_text.partition("\n")
            title = _strip_markdown_emphasis(first_line).strip()[:80]
            candidates.append((title, item_text))

        if candidates:
            break

    return [
        _make_node(
            node_id=f"step-{i + 1}",
            sub_type="step",
            title=title or f"Step {i + 1}",
            desc=f"步骤 {i + 1}",
            raw=raw,
            x=0,    # 上层重排
            y=_BASELINE_Y,
        )
        for i, (title, raw) in enumerate(candidates)
    ]


def _split_by_h3(content: str) -> list[tuple[re.Match[str], str]]:
    """按 ### 切；返回 [(heading_match, block_text)]。"""
    h3_re = re.compile(r"^###\s+.+?$", re.MULTILINE)
    matches = list(h3_re.finditer(content))
    out: list[tuple[re.Match[str], str]] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        out.append((m, content[start:end]))
    return out


_EMPHASIS_RE = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`")


def _strip_markdown_emphasis(text: str) -> str:
    return _EMPHASIS_RE.sub(lambda m: next(g for g in m.groups() if g), text)


def _strip_numbered(text: str) -> str:
    """掐掉 H3 上行的 ### 第 N 步 标题行；只留下方正文。"""
    lines = text.splitlines()
    return "\n".join(lines[1:]).strip() if lines else ""


# ────────── tool / subagent / resource 引用扫描 ──────────

# 行内 tool 名（反引号或裸名），匹配 \b 边界避免误中 file_writer 这种
_INLINE_TOOL_RE = re.compile(
    r"`(?P<bt>[A-Za-z_][A-Za-z0-9_]*)`|\b(?P<bare>[A-Z][a-zA-Z]+|[a-z]+_[a-z_]+)\b",
)


def _extract_frontmatter_tools(skill: SkillDef) -> list[str]:
    """frontmatter 里可能写 allowed-tools / tools 字段（humanizer 是个典型）。

    SkillDef 当前没透传 frontmatter，但 source 文件可重新读出 frontmatter；为避免循环依赖
    （loader 已经解析过），这里直接复用 frontmatter parser 二次解析。
    """
    try:
        from sanshiliu.foundation.frontmatter import parse as _fm_parse
        text = skill.source.read_text(encoding="utf-8")
        fm = _fm_parse(text).frontmatter
    except (OSError, ValueError):
        return []
    raw = fm.get("allowed-tools") or fm.get("tools") or []
    if not isinstance(raw, list):
        return []
    return [str(t).strip() for t in raw if isinstance(t, (str, int)) and str(t).strip()]


def _extract_tool_refs(text: str) -> list[str]:
    """从 step 正文里找 tool 名；返回有序去重列表（保留首次出现顺序）。"""
    found: list[str] = []
    seen: set[str] = set()
    for m in _INLINE_TOOL_RE.finditer(text):
        name = m.group("bt") or m.group("bare")
        if name in _KNOWN_TOOLS and name not in seen:
            found.append(name)
            seen.add(name)
    return found


# 引用同目录 agents/ 子文件
_SUBAGENT_REF_RE = re.compile(r"agents[/\\]([A-Za-z0-9_\-]+\.md)")


def _extract_subagent_refs(text: str, skill_dir: Any) -> list[str]:
    """找 'agents/X.md' 引用；返回 [文件名] 列表（去重保序）。"""
    found: list[str] = []
    seen: set[str] = set()
    for m in _SUBAGENT_REF_RE.finditer(text):
        fname = m.group(1)
        if fname not in seen:
            found.append(fname)
            seen.add(fname)
    return found


# 引用 references/ scripts/ assets/ 子文件 或 ~ 路径
_RESOURCE_REF_RE = re.compile(
    r"(?:references|scripts|assets)[/\\]([A-Za-z0-9_\-./]+)"
    r"|~/(?P<home>[A-Za-z0-9_\-./]+)",
)


def _extract_resource_refs(text: str, skill_dir: Any) -> list[str]:
    """找 references/ scripts/ assets/ 引用 + ~/path 引用。"""
    found: list[str] = []
    seen: set[str] = set()
    for m in _RESOURCE_REF_RE.finditer(text):
        fname = m.group(1) or m.group("home")
        if fname and fname not in seen:
            found.append(fname[:60])    # 截断防超长
            seen.add(fname)
    return found


# ────────── output 节点 ──────────

def _build_output(sections: list[dict[str, Any]], warnings: list[str]) -> dict[str, Any]:
    """先找显式 Output 段；找不到回退到 description 尾句 + 标 warning。"""
    for sec in sections:
        if _is_heading_match(sec["title"], _OUTPUT_HEADINGS):
            return _make_node(
                node_id="output",
                sub_type="output",
                title=sec["title"] or "输出",
                desc="输出格式",
                raw=sec["content"][:600],
                x=0,
                y=_BASELINE_Y,
            )
    warnings.append("未检测到 ## Output / ## Recommendation Format 段；输出节点为兜底虚化")
    return _make_node(
        node_id="output",
        sub_type="output",
        title="自由格式输出",
        desc="兜底",
        raw="此 skill 未声明输出格式，LLM 自由生成",
        x=0,
        y=_BASELINE_Y,
    )
