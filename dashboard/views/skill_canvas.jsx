/* SkillCanvas — 无限画布只读 viewer（Apple 设计语言，见 uploads/DESIGN.md）。

   拉 /api/skills/{id}/structure，渲染节点 + 边。所有节点统一 type='custom'，
   按 data.type 在 CustomNode 内部用 eyebrow 文本 + 整卡类型辨识色区分类型（NODE_THEMES）。

   视觉：18px 圆角 + 极淡类型底 + 稍深同色 1px 边 + 同色 eyebrow + 极轻双层浮卡阴影。

   props:
     skillId  string  目标 skill 的 id（目录名）
*/

const RF = window.ReactFlow || {};
// MarkerType: xyflow 命名空间导出，用于 edge 箭头；若该 namespace 没有则为 undefined → marker 不渲染但不报错
const { ReactFlow, Background, MiniMap, Controls, Handle, Position, MarkerType, useNodesState, useEdgesState } = RF;

// Dify 布局常量（见 research/dify-canvas-stack.md）
const NODE_WIDTH = 240;
const HANDLE_OFFSET = 8;

// 节点类型 → { band: minimap 缩略图用色（DESIGN 允许的功能性用色）,
//             eyebrow: 节点顶部大写标签,
//             bg/border/fg: 整卡极淡类型底 + 稍深同色边 + 同色 eyebrow（全用 dashboard.html 已有 token）}
const NODE_THEMES = {
  trigger:  { band: "var(--success)",       eyebrow: "TRIGGER",  bg: "var(--success-bg)",     border: "var(--success)",         fg: "var(--success-fg)" },
  step:     { band: "var(--primary)",       eyebrow: "STEP",     bg: "var(--primary-soft)",   border: "var(--primary)",         fg: "var(--primary)" },
  tool:     { band: "var(--warning)",       eyebrow: "TOOL",     bg: "var(--warning-bg)",     border: "var(--warning)",         fg: "var(--warning-fg)" },
  subagent: { band: "var(--primary-focus)", eyebrow: "SUBAGENT", bg: "var(--primary-soft-2)", border: "var(--primary-focus)",   fg: "var(--primary-focus)" },
  resource: { band: "var(--ink-48)",        eyebrow: "RESOURCE", bg: "var(--pearl)",          border: "var(--hairline-strong)", fg: "var(--ink-48)" },
  output:   { band: "var(--success)",       eyebrow: "OUTPUT",   bg: "var(--success-bg)",     border: "var(--success)",         fg: "var(--success-fg)" },
};

// 边 kind → 样式。默认 hairline；subagent/resource 保留虚线区分但同样走 hairline。
// 全部静态（移除 animated）——Apple 语境下不要流动动画。
const EDGE_STYLES = {
  sequence: { stroke: "var(--hairline-strong)", strokeWidth: 2 },
  anchor:   { stroke: "var(--hairline-strong)", strokeWidth: 2 },
  tool:     { stroke: "var(--hairline-strong)", strokeWidth: 2 },
  subagent: { stroke: "var(--hairline-strong)", strokeWidth: 2, strokeDasharray: "6 4" },
  resource: { stroke: "var(--hairline-strong)", strokeWidth: 1.5, strokeDasharray: "2 4" },
};


function CustomNode({ data, selected }) {
  const theme = NODE_THEMES[data.type] || NODE_THEMES.step;
  return (
    // 外层容器：白基底 + overflow hidden（裁掉 header 方角到 18px 圆角）+ 选中态边框 + 极轻浮卡阴影（双层：近距实影落地 + 远距软影漂浮，负 spread 收窄）；无 min-height（两区自然撑）
    <div style={{
      width: NODE_WIDTH,
      boxSizing: "border-box",
      background: "var(--canvas)",
      border: selected ? "2px solid var(--primary-focus)" : `1px solid ${theme.border}`,
      borderRadius: 18,
      overflow: "hidden",
      boxShadow: selected
        ? "0 2px 4px rgba(0,0,0,0.06), 0 12px 28px -8px rgba(0,0,0,0.16)"
        : "0 1px 2px rgba(0,0,0,0.04), 0 8px 20px -8px rgba(0,0,0,0.10)",
    }}>
      {/* header 区（上，tinted）：类型极淡色底 + 与 body 的 hairline 分隔线，放 eyebrow + title */}
      <div style={{
        background: theme.bg,
        padding: "10px 16px",
        borderBottom: "1px solid var(--hairline)",
      }}>
        {/* eyebrow：类型差异化（大写 / 12px / 600 / 0.06em tracking / 同类型色） */}
        <div style={{
          fontSize: 12, fontWeight: 600, letterSpacing: "0.06em",
          textTransform: "uppercase", color: theme.fg,
        }}>{theme.eyebrow}</div>
        {/* title：body-strong 17px / 600 / -0.374px */}
        <div style={{
          marginTop: 8,
          fontSize: 17, fontWeight: 600, lineHeight: 1.24, letterSpacing: "-0.374px",
          color: "var(--ink)",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>{data.title}</div>
      </div>
      {/* body 区（下，白）：放 desc；desc 为空则整个 body 不渲染 */}
      {data.desc && (
        <div style={{
          background: "var(--canvas)",
          padding: "10px 16px",
        }}>
          {/* desc：caption 14px / 400 / -0.224px / 灰 */}
          <div style={{
            fontSize: 14, fontWeight: 400, lineHeight: 1.43, letterSpacing: "-0.224px",
            color: "var(--ink-48)",
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          }}>{data.desc}</div>
        </div>
      )}
      {/* handle：严格 Right→Left；统一 hairline 色；缩进 ±8px。放外层容器内（不在 header/body 内），否则定位会错 */}
      <Handle type="target" position={Position.Left}  style={{ left: -HANDLE_OFFSET, background: "var(--hairline)", width: 8, height: 8 }} />
      <Handle type="source" position={Position.Right} style={{ right: -HANDLE_OFFSET, background: "var(--hairline)", width: 8, height: 8 }} />
    </div>
  );
}

const nodeTypes = { custom: CustomNode };


function SkillCanvas({ skillId }) {
  const [graph, setGraph] = React.useState(null);
  const [err, setErr] = React.useState(null);
  const [activeNode, setActiveNode] = React.useState(null);

  React.useEffect(() => {
    if (!skillId) return;
    setGraph(null); setErr(null); setActiveNode(null);
    API.get(`/api/skills/${encodeURIComponent(skillId)}/structure`).then(r => {
      if (r.error) setErr(r.error);
      else setGraph(r);
    });
  }, [skillId]);

  if (!ReactFlow) {
    return (
      <div style={{ padding: 40, textAlign: "center", color: "var(--ink-60)" }}>
        画布库未加载（window.ReactFlow 不存在）；请检查 vendor/xyflow-react.umd.js
      </div>
    );
  }
  if (err) {
    return <div style={{ padding: 40, textAlign: "center", color: "var(--danger)" }}>加载失败：{err}</div>;
  }
  if (!graph) {
    return <div style={{ padding: 40, textAlign: "center", color: "var(--ink-60)" }}>加载中…</div>;
  }

  // 后端给的 nodes 已经带 position；前端补 edge 的 style + 类型。
  // 后端把 edge 的 type 标为 "custom" 是占位（MVP 没注册自定义 edgeType）；这里显式设
  // smoothstep（圆角直角折线，配合横向 Right→Left 布局更清爽），加 ArrowClosed 箭头指明流向。
  const styledEdges = graph.edges.map((e) => {
    const kind = (e.data && e.data.kind);
    const style = EDGE_STYLES[kind] || EDGE_STYLES.sequence;
    const { type: _t, ...rest } = e;
    return {
      ...rest,
      type: "smoothstep",
      pathOptions: { borderRadius: 12 },
      // MarkerType 未导出时为 undefined → markerEnd.type=undefined → 箭头不渲染但不报错
      markerEnd: { type: MarkerType && MarkerType.ArrowClosed, width: 14, height: 14, color: style.stroke },
      style,
    };
  });

  return (
    <div style={{ width: "100%", height: "100%", position: "relative", background: "var(--parchment)" }}>
      <ReactFlow
        nodes={graph.nodes}
        edges={styledEdges}
        nodeTypes={nodeTypes}
        // 只读三连（参考 Dify hooks/use-workflow-mode.ts）：禁止连线/删除/编辑，
        // 但保留 selectable + draggable，让用户能手动整理布局
        nodesConnectable={false}
        elementsSelectable={true}
        nodesDraggable={true}
        deleteKeyCode={null}
        fitView
        fitViewOptions={{ padding: 0.22, minZoom: 0.4, maxZoom: 0.9 }}
        minZoom={0.25}
        maxZoom={2}
        onNodeClick={(_, node) => setActiveNode(node)}
        onPaneClick={() => setActiveNode(null)}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={20} size={1} color="var(--hairline-strong)" />
        <MiniMap pannable zoomable
          nodeColor={(n) => {
            const t = (n.data && n.data.type) || "step";
            return (NODE_THEMES[t] && NODE_THEMES[t].band) || "var(--ink-48)";
          }}
          nodeStrokeWidth={2}
          maskColor="rgba(245,245,247,0.55)"
          style={{ background: "var(--canvas)", border: "1px solid var(--hairline)" }}
        />
        <Controls showInteractive={false} />
      </ReactFlow>

      {/* 节点详情卡片：点节点 → 右下角浮出原文段落 */}
      {activeNode && <NodeInspector node={activeNode} onClose={() => setActiveNode(null)} />}
    </div>
  );
}


function NodeInspector({ node, onClose }) {
  const theme = NODE_THEMES[node.data.type] || NODE_THEMES.step;
  return (
    <div style={{
      position: "absolute", bottom: 12, right: 12,
      width: 360, maxHeight: "60%", display: "flex", flexDirection: "column",
      background: "var(--canvas)",
      border: "1px solid var(--hairline-strong)",
      borderRadius: 18,
      overflow: "hidden",
      boxShadow: "0 4px 24px -6px rgba(0,0,0,0.18)",
      zIndex: 10,
    }}>
      <div style={{
        padding: "16px 20px",
        background: theme.bg,
        borderBottom: "1px solid var(--hairline)",
        display: "flex", alignItems: "center", gap: 10,
      }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            fontSize: 12, fontWeight: 600, letterSpacing: "0.06em",
            textTransform: "uppercase", color: theme.fg,
          }}>{theme.eyebrow}</div>
          <div style={{
            marginTop: 4,
            fontSize: 17, fontWeight: 600, lineHeight: 1.24, letterSpacing: "-0.374px",
            color: "var(--ink)",
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          }}>{node.data.title}</div>
        </div>
        <button className="btn-icon" onClick={onClose} title="关闭"><Icon name="x" size={14}/></button>
      </div>
      <pre style={{
        margin: 0, padding: "16px 20px",
        fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.6,
        color: "var(--ink-80)", whiteSpace: "pre-wrap",
        overflow: "auto", flex: 1,
      }}>{node.data.raw || node.data.desc || "(无内容)"}</pre>
    </div>
  );
}

Object.assign(window, { SkillCanvas });
