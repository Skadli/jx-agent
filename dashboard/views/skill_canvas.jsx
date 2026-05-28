/* SkillCanvas — Dify 风格无限画布；只读 viewer。

   拉 /api/skills/{id}/structure，渲染节点 + 边。所有节点统一 type='custom'，
   按 data.type 在 CustomNode 内部分流形状 + 顶部色带（Dify 关键技巧）。

   props:
     skillId  string  目标 skill 的 id（目录名）
*/

const RF = window.ReactFlow || {};
const { ReactFlow, Background, MiniMap, Controls, Handle, Position, useNodesState, useEdgesState } = RF;

// Dify 视觉常量（见 research/dify-canvas-stack.md）
const NODE_WIDTH = 240;
const HANDLE_OFFSET = 8;

// 节点类型 → { 顶部色带, badge 文案 }
const NODE_THEMES = {
  trigger:  { band: "var(--success)",      label: "输入" },
  step:     { band: "var(--primary)",      label: "步骤" },
  tool:     { band: "var(--warning)",      label: "工具" },
  subagent: { band: "var(--primary-focus)", label: "子 Agent" },
  resource: { band: "var(--ink-48)",       label: "资源" },
  output:   { band: "var(--success)",      label: "输出" },
};

// 边 kind → 样式（虚线 / 颜色 / 粗细）
const EDGE_STYLES = {
  sequence: { stroke: "var(--ink-48)",     strokeWidth: 2 },
  anchor:   { stroke: "var(--primary)",    strokeWidth: 2 },
  tool:     { stroke: "var(--warning)",    strokeWidth: 2 },
  subagent: { stroke: "var(--primary-focus)", strokeWidth: 2, strokeDasharray: "6 4" },
  resource: { stroke: "var(--ink-48)",     strokeWidth: 1.5, strokeDasharray: "2 4" },
};


function CustomNode({ data }) {
  const theme = NODE_THEMES[data.type] || NODE_THEMES.step;
  return (
    <div style={{
      width: NODE_WIDTH,
      background: "var(--canvas)",
      border: "1px solid var(--hairline-strong)",
      borderRadius: "var(--r-md)",
      boxShadow: "0 1px 2px rgba(0,0,0,0.06)",
      overflow: "hidden",
    }}>
      {/* 顶部色带 */}
      <div style={{ height: 3, background: theme.band }} />
      {/* 节点正文 */}
      <div style={{ padding: "10px 12px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
          <span className="chip" style={{ fontSize: 10, padding: "1px 7px", background: theme.band, color: "#fff" }}>{theme.label}</span>
        </div>
        <div className="t-row-strong" style={{
          color: "var(--ink)",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>{data.title}</div>
        {data.desc && (
          <div className="t-meta" style={{
            marginTop: 3,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          }}>{data.desc}</div>
        )}
      </div>
      {/* handle：严格 Right→Left；缩进 ±8px */}
      <Handle type="target" position={Position.Left}  style={{ left: -HANDLE_OFFSET, background: "var(--ink-30)", width: 8, height: 8 }} />
      <Handle type="source" position={Position.Right} style={{ right: -HANDLE_OFFSET, background: "var(--ink-30)", width: 8, height: 8 }} />
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
      animated: e.data && (e.data.kind === "tool" || e.data.kind === "subagent"),
    };
  });

  return (
    <div style={{ width: "100%", height: "100%", position: "relative" }}>
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
        <Background gap={20} size={1} color="var(--hairline)" />
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

      {/* 启发式 warning 浮条 */}
      {graph.meta && graph.meta.warnings && graph.meta.warnings.length > 0 && (
        <div style={{
          position: "absolute", top: 12, left: 12,
          background: "var(--warning-bg)", color: "var(--warning-fg)",
          border: "1px solid var(--warning)",
          padding: "8px 12px", borderRadius: "var(--r-md)",
          fontSize: 12, maxWidth: 360, lineHeight: 1.5, zIndex: 5,
        }}>
          {graph.meta.warnings.map((w, i) => <div key={i}>· {w}</div>)}
        </div>
      )}

      {/* meta 摘要浮条 */}
      <div style={{
        position: "absolute", bottom: 12, left: 12,
        background: "rgba(255,255,255,0.85)",
        border: "1px solid var(--hairline)",
        padding: "5px 10px", borderRadius: "var(--r-pill)",
        fontSize: 11, color: "var(--ink-60)",
        backdropFilter: "saturate(180%) blur(20px)",
        zIndex: 5,
      }}>
        {graph.meta.step_count} step · {graph.meta.tool_count} tool · {graph.meta.subagent_count} subagent · {graph.meta.resource_count} resource
      </div>

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
      borderRadius: "var(--r-md)",
      boxShadow: "0 8px 32px -8px rgba(0,0,0,0.18)",
      zIndex: 10,
    }}>
      <div style={{ height: 3, background: theme.band, borderRadius: "var(--r-md) var(--r-md) 0 0" }} />
      <div style={{
        padding: "10px 14px",
        borderBottom: "1px solid var(--hairline)",
        display: "flex", alignItems: "center", gap: 8,
      }}>
        <span className="chip" style={{ fontSize: 10, padding: "1px 7px", background: theme.band, color: "#fff" }}>{theme.label}</span>
        <div className="t-row-strong" style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{node.data.title}</div>
        <button className="btn-icon" onClick={onClose} title="关闭"><Icon name="x" size={14}/></button>
      </div>
      <pre style={{
        margin: 0, padding: "12px 14px",
        fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.6,
        color: "var(--ink-80)", whiteSpace: "pre-wrap",
        overflow: "auto", flex: 1,
      }}>{node.data.raw || node.data.desc || "(无内容)"}</pre>
    </div>
  );
}

Object.assign(window, { SkillCanvas });
