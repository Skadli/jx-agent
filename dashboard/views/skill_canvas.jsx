/* SkillCanvas — 无限画布只读 viewer（Apple 设计语言，见 uploads/DESIGN.md）。

   拉 /api/skills/{id}/structure，渲染节点 + 边。所有节点统一 type='custom'，
   按 data.type 在 CustomNode 内部用 eyebrow 文本 + 整卡类型辨识色区分类型（NODE_THEMES）。

   视觉：18px 圆角 + 极淡类型底 + 稍深同色 1px 边 + 同色 eyebrow + 极轻双层浮卡阴影。

   props:
     skillId  string  目标 skill 的 id（目录名）
*/

const RF = window.ReactFlow || {};
// MarkerType: xyflow 命名空间导出，用于 edge 箭头；若该 namespace 没有则为 undefined → marker 不渲染但不报错
const { ReactFlow, Background, MiniMap, Controls, Handle, Position, MarkerType, Panel, useStore, useReactFlow } = RF;

// Dify 布局常量（见 research/dify-canvas-stack.md）
const NODE_WIDTH = 240;
const NODE_HEIGHT = 106;
const NODE_HEIGHT_COMPACT = 64;
const HANDLE_OFFSET = 8;

const MINI_COLORS = {
  canvas: "#ffffff",
  parchment: "#f5f5f7",
  pearl: "#fafafc",
  hairline: "#e8e8ed",
  hairlineStrong: "#d2d2d7",
  ink: "#1d1d1f",
  ink48: "#86868b",
  primary: "#0066cc",
};

// 节点类型 → { band: minimap 缩略图强调色（DESIGN 允许的功能性用色）,
//             eyebrow: 节点顶部大写标签,
//             bg/border/fg: 整卡极淡类型底 + 稍深同色边 + 同色 eyebrow（全用 dashboard.html 已有 token）}
const NODE_THEMES = {
  trigger:  { band: "#30a272", miniBg: "#e6f4ee", eyebrow: "TRIGGER",  bg: "var(--success-bg)",     border: "var(--success)",         miniBorder: "#30a272", fg: "var(--success-fg)" },
  step:     { band: "#0066cc", miniBg: "#ebf3fb", eyebrow: "STEP",     bg: "var(--primary-soft)",   border: "var(--primary)",         miniBorder: "#0066cc", fg: "var(--primary)" },
  tool:     { band: "#c89530", miniBg: "#fcf2dd", eyebrow: "TOOL",     bg: "var(--warning-bg)",     border: "var(--warning)",         miniBorder: "#c89530", fg: "var(--warning-fg)" },
  subagent: { band: "#0071e3", miniBg: "#dceeff", eyebrow: "SUBAGENT", bg: "var(--primary-soft-2)", border: "var(--primary-focus)",   miniBorder: "#0071e3", fg: "var(--primary-focus)" },
  resource: { band: "#86868b", miniBg: "#fafafc", eyebrow: "RESOURCE", bg: "var(--pearl)",          border: "var(--hairline-strong)", miniBorder: "#d2d2d7", fg: "var(--ink-48)" },
  output:   { band: "#30a272", miniBg: "#e6f4ee", eyebrow: "OUTPUT",   bg: "var(--success-bg)",     border: "var(--success)",         miniBorder: "#30a272", fg: "var(--success-fg)" },
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

function withNodeDimensions(nodes) {
  return (nodes || []).map((n) => ({
    ...n,
    initialWidth: n.initialWidth || n.width || NODE_WIDTH,
    initialHeight: n.initialHeight || n.height || ((n.data && n.data.desc) ? NODE_HEIGHT : NODE_HEIGHT_COMPACT),
  }));
}

function getNodeDimensions(n) {
  return {
    width: n.width || n.initialWidth || (n.measured && n.measured.width) || NODE_WIDTH,
    height: n.height || n.initialHeight || (n.measured && n.measured.height) || ((n.data && n.data.desc) ? NODE_HEIGHT : NODE_HEIGHT_COMPACT),
  };
}

function getMiniMapSnapshot(state) {
  const nodes = Array.from(state.nodeLookup.values())
    .map((internal) => {
      const userNode = internal.internals && internal.internals.userNode;
      if (!userNode || userNode.hidden) return null;
      const { width, height } = getNodeDimensions({
        ...userNode,
        measured: internal.measured,
      });
      const abs = (internal.internals && internal.internals.positionAbsolute) || userNode.position || { x: 0, y: 0 };
      return {
        id: userNode.id,
        x: abs.x,
        y: abs.y,
        width,
        height,
        data: userNode.data || {},
        selected: !!userNode.selected,
      };
    })
    .filter(Boolean);

  return {
    nodes,
    edges: state.edges || [],
    transform: state.transform || [0, 0, 1],
    flowWidth: state.width || 1,
    flowHeight: state.height || 1,
  };
}

function boundsFromRects(rects) {
  if (!rects.length) return null;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  rects.forEach((r) => {
    minX = Math.min(minX, r.x);
    minY = Math.min(minY, r.y);
    maxX = Math.max(maxX, r.x + r.width);
    maxY = Math.max(maxY, r.y + r.height);
  });
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}

function expandBounds(bounds, pad) {
  return {
    x: bounds.x - pad,
    y: bounds.y - pad,
    width: bounds.width + pad * 2,
    height: bounds.height + pad * 2,
  };
}

function miniEdgePath(edge, nodesById) {
  const source = nodesById.get(edge.source);
  const target = nodesById.get(edge.target);
  if (!source || !target) return null;
  const sx = source.x + source.width;
  const sy = source.y + source.height / 2;
  const tx = target.x;
  const ty = target.y + target.height / 2;
  const mx = sx + Math.max(48, (tx - sx) * 0.5);
  return `M ${sx} ${sy} C ${mx} ${sy}, ${mx} ${ty}, ${tx} ${ty}`;
}

function MiniCanvasNode({ node }) {
  const theme = NODE_THEMES[node.data.type] || NODE_THEMES.step;
  const r = Math.min(18, Math.max(8, Math.min(node.width, node.height) * 0.12));
  const headerH = Math.min(48, Math.max(30, node.height * (node.data.desc ? 0.42 : 0.62)));
  const padX = Math.max(14, node.width * 0.07);
  const accentW = Math.max(38, node.width * 0.22);
  const titleW = Math.max(72, node.width * 0.58);
  const bodyW = Math.max(68, node.width * 0.48);
  const stroke = node.selected ? MINI_COLORS.primary : theme.miniBorder;

  return (
    <g>
      <rect
        x={node.x}
        y={node.y}
        width={node.width}
        height={node.height}
        rx={r}
        ry={r}
        fill={MINI_COLORS.canvas}
      />
      <path
        d={`M ${node.x + r} ${node.y} H ${node.x + node.width - r} Q ${node.x + node.width} ${node.y} ${node.x + node.width} ${node.y + r} V ${node.y + headerH} H ${node.x} V ${node.y + r} Q ${node.x} ${node.y} ${node.x + r} ${node.y} Z`}
        fill={theme.miniBg}
      />
      <line
        x1={node.x}
        y1={node.y + headerH}
        x2={node.x + node.width}
        y2={node.y + headerH}
        stroke={MINI_COLORS.hairline}
        strokeWidth={1}
        vectorEffect="non-scaling-stroke"
      />
      <rect
        x={node.x + padX}
        y={node.y + Math.max(11, headerH * 0.25)}
        width={accentW}
        height={5}
        rx={2.5}
        fill={theme.band}
        opacity={0.9}
      />
      <rect
        x={node.x + padX}
        y={node.y + Math.max(24, headerH * 0.58)}
        width={titleW}
        height={7}
        rx={3.5}
        fill={MINI_COLORS.ink}
        opacity={0.45}
      />
      {node.data.desc && (
        <rect
          x={node.x + padX}
          y={node.y + headerH + Math.max(14, (node.height - headerH) * 0.35)}
          width={bodyW}
          height={6}
          rx={3}
          fill={MINI_COLORS.ink48}
          opacity={0.42}
        />
      )}
      <rect
        x={node.x}
        y={node.y}
        width={node.width}
        height={node.height}
        rx={r}
        ry={r}
        fill="none"
        stroke={stroke}
        strokeWidth={node.selected ? 2 : 1}
        vectorEffect="non-scaling-stroke"
      />
    </g>
  );
}

function SkillMiniMap() {
  const snapshot = useStore(getMiniMapSnapshot);
  const flow = useReactFlow();
  const svgRef = React.useRef(null);

  const nodesById = React.useMemo(() => {
    const m = new Map();
    snapshot.nodes.forEach((n) => m.set(n.id, n));
    return m;
  }, [snapshot.nodes]);

  const viewport = React.useMemo(() => {
    const z = Math.max(snapshot.transform[2] || 1, 0.0001);
    return {
      x: -snapshot.transform[0] / z,
      y: -snapshot.transform[1] / z,
      width: snapshot.flowWidth / z,
      height: snapshot.flowHeight / z,
    };
  }, [snapshot.transform, snapshot.flowWidth, snapshot.flowHeight]);

  const viewBox = React.useMemo(() => {
    const nodeBounds = boundsFromRects(snapshot.nodes);
    const content = nodeBounds ? boundsFromRects([nodeBounds, viewport]) : viewport;
    const pad = Math.max(96, Math.max(content.width, content.height) * 0.08);
    return expandBounds(content, pad);
  }, [snapshot.nodes, viewport]);

  const handleClick = React.useCallback((event) => {
    if (!svgRef.current || !flow) return;
    event.stopPropagation();
    const point = svgRef.current.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    const ctm = svgRef.current.getScreenCTM();
    if (!ctm) return;
    const target = point.matrixTransform(ctm.inverse());
    flow.setCenter(target.x, target.y, {
      zoom: Math.max(0.35, Math.min(1.1, snapshot.transform[2] || 0.7)),
      duration: 220,
    });
  }, [flow, snapshot.transform]);

  if (!snapshot.nodes.length) return null;

  const outerPath = `M ${viewBox.x} ${viewBox.y} H ${viewBox.x + viewBox.width} V ${viewBox.y + viewBox.height} H ${viewBox.x} Z`;
  const viewportPath = `M ${viewport.x} ${viewport.y} H ${viewport.x + viewport.width} V ${viewport.y + viewport.height} H ${viewport.x} Z`;

  return (
    <Panel position="bottom-right" style={{ margin: 12 }}>
      <div
        onMouseDown={(e) => e.stopPropagation()}
        onWheel={(e) => e.stopPropagation()}
        style={{
          width: 224,
          height: 164,
          background: MINI_COLORS.canvas,
          border: `1px solid ${MINI_COLORS.hairline}`,
          borderRadius: 14,
          overflow: "hidden",
          boxShadow: "0 1px 2px rgba(0,0,0,0.04), 0 10px 28px -14px rgba(0,0,0,0.22)",
        }}
      >
        <svg
          ref={svgRef}
          width="224"
          height="164"
          viewBox={`${viewBox.x} ${viewBox.y} ${viewBox.width} ${viewBox.height}`}
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-label="画布缩略图"
          onClick={handleClick}
          style={{ display: "block", cursor: "pointer" }}
        >
          <defs>
            <pattern id="skill-minimap-grid" width="20" height="20" patternUnits="userSpaceOnUse">
              <circle cx="1" cy="1" r="1" fill={MINI_COLORS.hairlineStrong} opacity="0.75" />
            </pattern>
          </defs>
          <rect x={viewBox.x} y={viewBox.y} width={viewBox.width} height={viewBox.height} fill={MINI_COLORS.parchment} />
          <rect x={viewBox.x} y={viewBox.y} width={viewBox.width} height={viewBox.height} fill="url(#skill-minimap-grid)" />
          <g fill="none" stroke={MINI_COLORS.hairlineStrong} strokeWidth="2" vectorEffect="non-scaling-stroke">
            {snapshot.edges.map((edge) => {
              const path = miniEdgePath(edge, nodesById);
              if (!path) return null;
              const style = EDGE_STYLES[(edge.data && edge.data.kind) || "sequence"] || EDGE_STYLES.sequence;
              return (
                <path
                  key={edge.id}
                  d={path}
                  stroke={MINI_COLORS.hairlineStrong}
                  strokeDasharray={style.strokeDasharray || undefined}
                  opacity={0.95}
                />
              );
            })}
          </g>
          <g>
            {snapshot.nodes.map((node) => <MiniCanvasNode key={node.id} node={node} />)}
          </g>
          <path d={`${outerPath} ${viewportPath}`} fill={MINI_COLORS.canvas} fillOpacity="0.62" fillRule="evenodd" pointerEvents="none" />
          <rect
            x={viewport.x}
            y={viewport.y}
            width={viewport.width}
            height={viewport.height}
            fill="none"
            stroke={MINI_COLORS.primary}
            strokeWidth="2"
            rx="8"
            ry="8"
            vectorEffect="non-scaling-stroke"
            pointerEvents="none"
          />
        </svg>
      </div>
    </Panel>
  );
}

function MiniMapCardNode({ id, x, y, width, height, color, strokeColor, selected, onClick }) {
  return (
    <g onClick={onClick ? (e) => onClick(e, id) : undefined}>
      <MiniCanvasNode node={{
        id,
        x,
        y,
        width,
        height,
        data: { type: "step", desc: true },
        selected,
      }} />
      <rect x={x} y={y} width={width} height={Math.max(24, height * 0.42)} rx={8} ry={8} fill={color || MINI_COLORS.pearl} opacity={0.86} />
      <rect x={x} y={y} width={width} height={height} rx={8} ry={8} fill="none" stroke={strokeColor || MINI_COLORS.hairlineStrong} strokeWidth={selected ? 2 : 1} vectorEffect="non-scaling-stroke" />
    </g>
  );
}


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

  const flowNodes = React.useMemo(() => graph ? withNodeDimensions(graph.nodes) : [], [graph]);

  // 后端给的 nodes 已经带 position；前端补 edge 的 style + 类型。
  // 后端把 edge 的 type 标为 "custom" 是占位（MVP 没注册自定义 edgeType）；这里显式设
  // smoothstep（圆角直角折线，配合横向 Right→Left 布局更清爽），加 ArrowClosed 箭头指明流向。
  const styledEdges = React.useMemo(() => {
    if (!graph) return [];
    return graph.edges.map((e) => {
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
  }, [graph]);

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

  return (
    <div style={{ width: "100%", height: "100%", position: "relative", background: "var(--parchment)" }}>
      <ReactFlow
        nodes={flowNodes}
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
        {Panel && useStore && useReactFlow ? (
          <SkillMiniMap />
        ) : (
          <MiniMap pannable zoomable
            nodeComponent={MiniMapCardNode}
            nodeColor={(n) => {
              const t = (n.data && n.data.type) || "step";
              return (NODE_THEMES[t] && NODE_THEMES[t].miniBg) || MINI_COLORS.pearl;
            }}
            nodeStrokeColor={(n) => {
              const t = (n.data && n.data.type) || "step";
              return (NODE_THEMES[t] && NODE_THEMES[t].miniBorder) || MINI_COLORS.hairlineStrong;
            }}
            nodeStrokeWidth={1}
            nodeBorderRadius={8}
            bgColor={MINI_COLORS.parchment}
            maskColor="rgba(255,255,255,0.62)"
            style={{ background: MINI_COLORS.canvas, border: `1px solid ${MINI_COLORS.hairline}`, borderRadius: 14, overflow: "hidden" }}
          />
        )}
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
