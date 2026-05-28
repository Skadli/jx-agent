/* SkillCanvas — 无限画布只读 viewer（Apple 设计语言，见 uploads/DESIGN.md）。

   拉 /api/skills/{id}/structure，渲染节点 + 边。所有节点统一 type='custom'，
   按 data.type 在 CustomNode 内部用顶部 eyebrow 文本区分类型（不再用彩色色带）。

   视觉：18px 圆角 + 1px hairline + 纯白底 + 无 box-shadow；单一 Action Blue accent。

   props:
     skillId  string  目标 skill 的 id（目录名）
*/

const RF = window.ReactFlow || {};
const { ReactFlow, Background, MiniMap, Controls, Handle, Position, useNodesState, useEdgesState } = RF;

// Dify 布局常量（见 research/dify-canvas-stack.md）
const NODE_WIDTH = 240;
const HANDLE_OFFSET = 8;

// 节点类型 → { band: minimap 缩略图用色（DESIGN 允许的功能性用色，唯一例外）,
//             eyebrow: 节点顶部大写标签 }
const NODE_THEMES = {
  trigger:  { band: "var(--success)",       eyebrow: "TRIGGER" },
  step:     { band: "var(--primary)",       eyebrow: "STEP" },
  tool:     { band: "var(--warning)",       eyebrow: "TOOL" },
  subagent: { band: "var(--primary-focus)", eyebrow: "SUBAGENT" },
  resource: { band: "var(--ink-48)",        eyebrow: "RESOURCE" },
  output:   { band: "var(--success)",       eyebrow: "OUTPUT" },
};

// 边 kind → 样式。默认 hairline；subagent/resource 保留虚线区分但同样走 hairline。
// 全部静态（移除 animated）——Apple 语境下不要流动动画。
const EDGE_STYLES = {
  sequence: { stroke: "var(--hairline)", strokeWidth: 2 },
  anchor:   { stroke: "var(--hairline)", strokeWidth: 2 },
  tool:     { stroke: "var(--hairline)", strokeWidth: 2 },
  subagent: { stroke: "var(--hairline)", strokeWidth: 2, strokeDasharray: "6 4" },
  resource: { stroke: "var(--hairline)", strokeWidth: 1.5, strokeDasharray: "2 4" },
};


function CustomNode({ data, selected }) {
  const theme = NODE_THEMES[data.type] || NODE_THEMES.step;
  return (
    <div style={{
      width: NODE_WIDTH,
      minHeight: 72,
      boxSizing: "border-box",
      padding: "18px 20px",
      background: "var(--canvas)",
      border: selected ? "2px solid var(--primary-focus)" : "1px solid var(--hairline-strong)",
      borderRadius: 18,
    }}>
      {/* eyebrow：类型差异化唯一来源（大写 / 12px / 600 / 0.06em tracking / 灰） */}
      <div style={{
        fontSize: 12, fontWeight: 600, letterSpacing: "0.06em",
        textTransform: "uppercase", color: "var(--ink-48)",
      }}>{theme.eyebrow}</div>
      {/* title：body-strong 17px / 600 / -0.374px */}
      <div style={{
        marginTop: 8,
        fontSize: 17, fontWeight: 600, lineHeight: 1.24, letterSpacing: "-0.374px",
        color: "var(--ink)",
        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
      }}>{data.title}</div>
      {/* desc：caption 14px / 400 / -0.224px / 灰 */}
      {data.desc && (
        <div style={{
          marginTop: 4,
          fontSize: 14, fontWeight: 400, lineHeight: 1.43, letterSpacing: "-0.224px",
          color: "var(--ink-48)",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>{data.desc}</div>
      )}
      {/* handle：严格 Right→Left；统一 hairline 色；缩进 ±8px */}
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

  // 后端给的 nodes 已经带 position；前端只补 edge 的 style。
  // 后端把 edge 的 type 标为 "custom" 是为了将来扩展自定义边组件用，但 MVP 没注册
  // 自定义 edgeType，直接走 ReactFlow 默认 (bezier) 即可——所以删 type 字段。
  const styledEdges = graph.edges.map((e) => {
    const { type: _t, ...rest } = e;
    return {
      ...rest,
      style: EDGE_STYLES[e.data && e.data.kind] || EDGE_STYLES.sequence,
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
      zIndex: 10,
    }}>
      <div style={{
        padding: "16px 20px",
        borderBottom: "1px solid var(--hairline)",
        display: "flex", alignItems: "center", gap: 10,
      }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            fontSize: 12, fontWeight: 600, letterSpacing: "0.06em",
            textTransform: "uppercase", color: "var(--ink-48)",
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
