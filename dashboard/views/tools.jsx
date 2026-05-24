/* Tools — registry + recent tool call audit + lightweight analytics. */

function Tools({ onJump }) {
  const [tools, setTools] = React.useState([]);
  const [enabled, setEnabled] = React.useState(false);
  const [calls, setCalls] = React.useState([]);
  const [selectedId, setSelectedId] = React.useState(null);
  const [status, setStatus] = React.useState("loading");
  const [page, setPage] = React.useState(1);

  const refresh = React.useCallback(async () => {
    setStatus("loading");
    const [t, c] = await Promise.all([
      API.get("/api/tools"),
      API.get("/api/tool_calls?limit=200"),
    ]);
    if (!t.error) {
      setTools(t.tools || []);
      setEnabled(!!t.enabled);
    }
    if (!c.error) {
      const rows = c.tool_calls || [];
      setCalls(rows);
      setSelectedId(id => id || (rows[0] && rows[0].id) || null);
    }
    setStatus(t.error || c.error ? "error" : "ready");
  }, []);

  React.useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  const stats = summarizeToolCalls(calls);
  const selected = calls.find(c => c.id === selectedId) || calls[0] || null;
  const toolCounts = calls.reduce((acc, c) => {
    acc[c.tool_name] = (acc[c.tool_name] || 0) + 1;
    return acc;
  }, {});

  // 每页 5 条 + 分页：数据刷新后若当前页超出范围自动收回
  const PAGE_SIZE = 5;
  const totalPages = Math.max(1, Math.ceil(calls.length / PAGE_SIZE));
  React.useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);
  const pageStart = (page - 1) * PAGE_SIZE;
  const pageRows = calls.slice(pageStart, pageStart + PAGE_SIZE);

  return (
    <div data-screen-label="08 工具">
      <PageHeader
        title="工具"
        sub={`${tools.length} 个已注册 · ${calls.length} 条调用 · ${enabled ? "工具栈启用" : "工具栈未启用"}`}
        actions={
          <>
            <button className="btn btn-secondary" onClick={refresh}><Icon name="refresh" size={13}/>刷新</button>
            <button className="btn btn-secondary" onClick={() => {
              const jsonl = calls.map(c => JSON.stringify(c)).join("\n");
              API.download("tool_calls.jsonl", jsonl);
            }}><Icon name="download" size={13}/>导出调用</button>
            <button className="btn btn-primary" onClick={() => onJump("permissions")}><Icon name="lock" size={13} color="#fff"/>权限</button>
          </>
        } />

      <div className="page-body">
        <div className="grid-4">
          <StatCard label="注册工具" value={tools.length} sub={enabled ? "可供模型调用" : "当前未启用"} />
          <StatCard label="调用次数" value={calls.length} sub="最近 200 条" />
          <StatCard label="错误率" value={`${stats.errorRate}%`} sub={`${stats.errors} 次错误`} color={stats.errors ? "var(--danger)" : "var(--ink)"} />
          <StatCard label="平均耗时" value={stats.avgLatency} unit="ms" sub={stats.topTool ? `最高频：${stats.topTool}` : "暂无调用"} />
        </div>

        <div className="tools-grid">
          <div className="card">
            <CardHeader title="工具列表" sub={status === "loading" ? "加载中…" : `${tools.length} 个`} />
            <div style={{ padding: 4 }}>
              {tools.length === 0 ? (
                <div className="t-meta" style={{ padding: 24, textAlign: "center" }}>暂无注册工具</div>
              ) : tools.map((tool) => (
                <div key={tool.name} style={{ padding: "12px 14px", borderTop: "1px solid var(--divider-soft)" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <Icon name="terminal" size={14} color="var(--primary)" />
                    <span className="t-mono-strong" style={{ color: "var(--ink)" }}>{tool.name}</span>
                    <span className="chip" style={{ marginLeft: "auto" }}>{toolCounts[tool.name] || 0} 次</span>
                  </div>
                  <div className="t-meta" style={{ marginTop: 6, color: "var(--ink-60)" }}>{tool.description}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="card">
            <CardHeader
              title="调用详情"
              sub={`第 ${page} / ${totalPages} 页 · 累计 ${calls.length}`}
              right={<span className="t-mono-sm">5s 刷新</span>} />
            <ResponsiveTable
              rows={pageRows}
              rowKey={c => c.id}
              onRowClick={c => setSelectedId(c.id)}
              isRowActive={c => selected && selected.id === c.id}
              emptyText="暂无工具调用"
              cardMinWidth={520}
              columns={[
                { key: "ts",        label: "时间",  width: 90,  mono: true,
                  render: c => <span style={{ color: "var(--ink-60)" }}>{API.relTime(c.ts)}</span> },
                { key: "tool_name", label: "工具",  width: 130, mono: true,
                  render: c => <span style={{ color: "var(--ink)" }}>{c.tool_name}</span> },
                { key: "arguments", label: "参数",  mono: true,
                  render: c => (
                    <span style={{
                      display: "inline-block", maxWidth: "100%",
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                    }}>{c.arguments || "{}"}</span>
                  )},
                { key: "is_error",  label: "状态",  width: 90,
                  render: c => c.is_error
                    ? <span className="chip chip-danger">错误</span>
                    : <span className="chip chip-success">ok</span> },
                { key: "latency_ms",label: "耗时",  width: 80, align: "right", mono: true,
                  render: c => `${c.latency_ms}ms` },
              ]} />
            <Pagination
              page={page}
              totalPages={totalPages}
              onChange={setPage}
              info={calls.length === 0
                ? ""
                : `${pageStart + 1}–${Math.min(pageStart + PAGE_SIZE, calls.length)} 条 / 共 ${calls.length}`} />
          </div>

          <ToolInspector call={selected} tools={tools} />
        </div>
      </div>
    </div>
  );
}

function ToolInspector({ call, tools }) {
  if (!call) {
    return <div className="card"><div className="card-body t-meta">选择一条调用查看参数和结果。</div></div>;
  }
  const definition = tools.find(t => t.name === call.tool_name);
  return (
    <div className="card">
      <CardHeader
        title={call.tool_name}
        sub={`#${call.id} · ${(call.session_id || "").slice(0, 18)}`}
        right={call.is_error ? <span className="chip chip-danger">错误</span> : <span className="chip chip-success">ok</span>}
      />
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <KV k="时间" v={API.relTime(call.ts)} />
        <KV k="耗时" v={`${call.latency_ms}ms`} />
        <KV k="权限" v={call.permission_decision || "—"} />
        {definition && <div className="t-meta" style={{ color: "var(--ink-60)" }}>{definition.description}</div>}
        <CodeBlock title="参数" text={formatJsonish(call.arguments)} />
        <CodeBlock title="结果" text={call.result_text || "—"} />
      </div>
    </div>
  );
}

function CodeBlock({ title, text }) {
  return (
    <div>
      <div className="t-meta-strong" style={{ marginBottom: 6 }}>{title}</div>
      <pre style={{
        margin: 0,
        maxHeight: 220,
        overflow: "auto",
        background: "var(--pearl)",
        border: "1px solid var(--hairline)",
        borderRadius: 8,
        padding: 12,
        fontFamily: "var(--font-mono)",
        fontSize: 11.5,
        lineHeight: 1.55,
        whiteSpace: "pre-wrap",
        color: "var(--ink-80)",
      }}>{text}</pre>
    </div>
  );
}

function summarizeToolCalls(calls) {
  const total = calls.length;
  const errors = calls.filter(c => c.is_error).length;
  const avgLatency = total ? Math.round(calls.reduce((sum, c) => sum + (Number(c.latency_ms) || 0), 0) / total) : 0;
  const counts = calls.reduce((acc, c) => {
    acc[c.tool_name] = (acc[c.tool_name] || 0) + 1;
    return acc;
  }, {});
  const topTool = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
  return {
    errors,
    avgLatency,
    errorRate: total ? Math.round(errors / total * 100) : 0,
    topTool: topTool ? topTool[0] : "",
  };
}

function formatJsonish(value) {
  if (!value) return "{}";
  try { return JSON.stringify(JSON.parse(value), null, 2); }
  catch (e) { return String(value); }
}

Object.assign(window, { Tools });
