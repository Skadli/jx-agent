/* Overview — admin dashboard.
 * 数据全部走 /api/overview /api/sessions /api/tool_calls /api/skills /api/memory /api/health。
 * 5s 轮询；range 切换重新拉。
 */

function Overview({ onJump }) {
  const [range, setRange] = React.useState("24h");
  const [overview, setOverview] = React.useState(null);
  const [sessions, setSessions] = React.useState([]);
  const [tools, setTools]       = React.useState([]);
  const [skills, setSkills]     = React.useState([]);
  const [memory, setMemory]     = React.useState({ entries: [], claudemd: null });
  const [health, setHealth]     = React.useState(null);

  const refresh = React.useCallback(async () => {
    const [o, s, t, sk, m, h] = await Promise.all([
      API.get(`/api/overview?range=${range}`),
      API.get(`/api/sessions?limit=8`),
      API.get(`/api/tool_calls?limit=8`),
      API.get(`/api/skills`),
      API.get(`/api/memory`),
      API.get(`/api/health`),
    ]);
    if (!o.error) setOverview(o);
    if (!s.error) setSessions(s.sessions || []);
    if (!t.error) setTools(t.tool_calls || []);
    if (!sk.error) setSkills(sk.skills || []);
    if (!m.error) setMemory(m);
    if (!h.error) setHealth(h);
  }, [range]);

  React.useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  const stats = overview && overview.stats;
  const ident = overview && overview.identity;
  const chMap = (stats && stats.channels) || {};
  const tot = stats ? (stats.input_tokens + stats.output_tokens) : 0;

  const onReload = async () => {
    const r = await API.post("/api/instance/reload");
    if (r.error) alert("重载失败：" + r.error);
    else { alert("重载已触发"); refresh(); }
  };

  return (
    <div data-screen-label="01 总览">
      <PageHeader
        title="总览"
        sub={overview
          ? `三十六贱笑 · v${overview.version} · 运行 ${Math.floor(overview.uptime_sec/60)} 分钟`
          : "加载中…"}
        actions={
          <>
            <Segmented value={range} onChange={setRange} options={[
              { id: "1h", label: "1 小时" },
              { id: "24h", label: "24 小时" },
              { id: "7d", label: "7 天" },
              { id: "30d", label: "30 天" },
            ]} />
            <button className="btn btn-secondary" onClick={refresh}><Icon name="refresh" size={13} />刷新</button>
            <button className="btn btn-primary" onClick={() => onJump("chat")}><Icon name="terminal" size={13} color="#fff" />打开会话</button>
          </>
        } />

      <div className="page-body">
        <div className="grid-4">
          <StatCard
            label="会话总数"
            value={stats ? stats.total_sessions : "—"}
            sub={Object.entries(chMap).map(([k, v]) => `${k} · ${v}`).join("  ") || "暂无活跃通道"} />
          <StatCard
            label="累计 tokens"
            value={stats ? API.fmtNumber(tot) : "—"}
            sub={stats ? `输入 ${API.fmtNumber(stats.input_tokens)} · 输出 ${API.fmtNumber(stats.output_tokens)}` : ""} />
          <StatCard
            label="累计成本"
            value={stats ? API.fmtCost(stats.cost_cny) : "—"}
            unit="￥"
            sub={`窗口：${range}`} />
          <StatCard
            label="平均延迟"
            value={stats ? (stats.avg_latency_ms / 1000).toFixed(2) : "—"}
            unit="s"
            sub={`${stats ? stats.calls : 0} 次调用`} />
        </div>

        <div className="grid-sessions">
          <SessionsCard sessions={sessions} onJump={onJump} />
          <IdentityCard overview={overview} ident={ident} onJump={onJump} onReload={onReload} />
        </div>

        <div style={{ marginTop: 16 }}>
          <ToolCallsCard tools={tools} onJump={onJump} />
        </div>

        <div className="grid-3" style={{ marginTop: 16 }}>
          <SkillsPreview skills={skills} onJump={onJump} />
          <MemoryPreview memory={memory} onJump={onJump} />
          <HealthCard health={health} />
        </div>
      </div>
    </div>);
}

/* ===================== CARDS ===================== */

function SessionsCard({ sessions, onJump }) {
  return (
    <div className="card">
      <CardHeader
        title="最近会话"
        sub={`${sessions.length} 条`}
        right={
          <>
            <button className="btn btn-ghost btn-sm" onClick={() => {
              const csv = "id,channel,calls,input_tokens,output_tokens,cost,last_active_at,last_message\n" +
                sessions.map(s => [s.id, s.channel, s.calls, s.input_tokens, s.output_tokens, s.cost_cny, s.last_active_at, JSON.stringify(s.last_message || "")].join(",")).join("\n");
              API.download("sessions.csv", csv);
            }}>导出</button>
            <button className="btn btn-secondary btn-sm" onClick={() => onJump("chat")}>查看全部</button>
          </>
        } />
      <ResponsiveTable
        rows={sessions}
        rowKey={s => s.id}
        onRowClick={() => onJump("chat")}
        emptyText="暂无会话"
        cardMinWidth={640}
        columns={[
          { key: "id", label: "会话 ID", width: 200, mono: true,
            render: s => <span style={{ color: "var(--ink)" }}>{s.id.slice(0, 14)}</span> },
          { key: "channel", label: "通道", width: 70,
            render: s => (
              <span className={`chip ${s.channel === "wechat" ? "" : s.channel === "web" ? "chip-info" : "chip-success"}`}
                style={s.channel === "wechat" ? { background: "rgba(193,60,123,0.10)", color: "#9c2f5f" } : {}}>
                {s.channel}
              </span>
            )},
          { key: "last_message", label: "最后一句",
            render: s => (
              <span style={{
                display: "inline-block", maxWidth: "100%",
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                color: "var(--ink)", fontWeight: 500,
              }}>{s.last_message || "—"}</span>
            )},
          { key: "tokens", label: "tokens", width: 100, align: "right", mono: true,
            render: s => API.fmtNumber(s.input_tokens + s.output_tokens) },
          { key: "cost", label: "成本", width: 80, align: "right", mono: true,
            render: s => `￥${API.fmtCost(s.cost_cny)}` },
          { key: "ts", label: "时间", width: 100, align: "right",
            render: s => <span style={{ color: "var(--ink-60)" }}>{API.relTime(s.last_active_at)}</span> },
        ]} />
    </div>);
}

function IdentityCard({ overview, ident, onJump, onReload }) {
  if (!overview || !ident) {
    return <div className="card"><div className="card-body t-meta">加载中…</div></div>;
  }
  const lines = [
    "╔══════════════════════════════════╗",
    "║  三十六贱笑 v" + overview.version.padEnd(20, " ") + "║",
    "╠══════════════════════════════════╣",
    "║  Model    " + (overview.model || "—").slice(0, 22).padEnd(22, " ") + " ║",
    "║  Base     " + ((overview.base_url || "—").replace(/^https?:\/\//, "")).slice(0, 22).padEnd(22, " ") + " ║",
    "║  Persona  " + `${API.fmtNumber(ident.persona_chars)} 字 / ${ident.persona_files} 份`.padEnd(22, " ") + " ║",
    "║  Skills   " + `${ident.skills_count} 个`.padEnd(22, " ") + " ║",
    "║  Memory   " + `${API.fmtNumber(ident.claudemd_chars)} 字 / ${ident.memdir_count} 条`.padEnd(22, " ") + " ║",
    "╚══════════════════════════════════╝",
  ].join("\n");

  return (
    <div className="card">
      <CardHeader title="实例" right={<span className="chip chip-success chip-dot">运行中</span>} />
      <div className="card-body">
        <pre className="shadow-product" style={{
          margin: 0, background: "var(--tile-1)", color: "var(--on-dark)",
          fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.55,
          padding: "16px 18px", borderRadius: 10, whiteSpace: "pre", overflow: "auto",
        }}>{lines}</pre>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 16 }}>
          <KV k="运行时长" v={`${Math.floor(overview.uptime_sec / 60)} 分钟`} />
          <KV k="模型" v={overview.model || "—"} />
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
          <button className="btn btn-secondary btn-sm grow" onClick={() => onJump("chat")}><Icon name="chat" size={13}/>打开会话</button>
          <button className="btn btn-secondary btn-sm grow" onClick={onReload}><Icon name="refresh" size={13}/>重启实例</button>
        </div>
      </div>
    </div>);
}

function HealthCard({ health }) {
  const comp = (health && health.components) || {};
  const probes = [
    { label: "Web",     status: comp.web === "up" ? "up" : "down",       value: comp.web || "?" },
    { label: "DB",      status: comp.db === "up" ? "up" : "down",        value: comp.db || "?" },
    { label: "LLM",     status: comp.llm === "up" ? "up" : (comp.llm === "unknown" ? "warn" : "down"), value: comp.llm || "?" },
    { label: "微信",
      status: comp.wechat === "up" ? "up"
            : comp.wechat === "disabled" ? "off"
            : comp.wechat === "expired" ? "down"
            : "warn",
      value: comp.wechat === "expired" ? "expired · 需重新扫码" : (comp.wechat || "?") },
  ];
  return (
    <div className="card">
      <CardHeader title="组件健康" sub="探针 · 5s 刷新" right={<span className="t-mono-sm" style={{ color: "var(--ink-60)" }}>实时</span>} />
      <div className="card-body" style={{ paddingTop: 6 }}>
        {probes.map((p) => <StatusRow key={p.label} {...p} />)}
      </div>
    </div>);
}

function ToolCallsCard({ tools, onJump }) {
  return (
    <div className="card">
      <CardHeader
        title="最近工具调用"
        sub={`${tools.length} 条`}
        right={
          <>
            <button className="btn btn-ghost btn-sm" onClick={() => onJump("tools")}><Icon name="filter" size={13} />筛选</button>
            <button className="btn btn-secondary btn-sm" onClick={() => onJump("tools")}>完整审计</button>
          </>
        } />
      <ResponsiveTable
        rows={tools}
        rowKey={c => c.id}
        emptyText="暂无工具调用"
        cardMinWidth={640}
        columns={[
          { key: "ts", label: "时间", width: 100, mono: true,
            render: c => <span style={{ color: "var(--ink-60)" }}>{API.relTime(c.ts)}</span> },
          { key: "tool_name", label: "工具", width: 130, mono: true,
            render: c => <span style={{ color: "var(--ink)" }}>{c.tool_name}</span> },
          { key: "arguments", label: "参数", mono: true,
            render: c => (
              <span style={{
                display: "inline-block", maxWidth: "100%",
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                color: "var(--ink-80)",
              }}>{c.arguments}</span>
            )},
          { key: "session_id", label: "会话", width: 160, mono: true,
            render: c => <span style={{ color: "var(--ink-60)" }}>{(c.session_id || "").slice(0, 14)}</span> },
          { key: "result", label: "结果", width: 100,
            render: c => c.is_error
              ? <span className="chip chip-danger"><Icon name="x" size={11} />错误</span>
              : <span className="chip chip-success"><Icon name="check" size={11} />ok</span> },
          { key: "latency_ms", label: "耗时", width: 70, align: "right", mono: true,
            render: c => `${c.latency_ms}ms` },
        ]} />
    </div>);
}

function SkillsPreview({ skills, onJump }) {
  return (
    <div className="card">
      <CardHeader
        title="技能"
        sub={`${skills.length} 个已注册`}
        right={<button className="btn btn-ghost btn-sm" onClick={() => onJump("skills")}>管理 →</button>} />
      <div style={{ padding: 4 }}>
        {skills.length === 0
          ? <div style={{ padding: "24px 16px", textAlign: "center", color: "var(--ink-60)" }} className="t-meta">暂无 skill</div>
          : skills.slice(0, 4).map((s, i) => (
              <div key={s.id} style={{ display: "flex", alignItems: "center", padding: "12px 16px", borderTop: i === 0 ? "none" : "1px solid var(--divider-soft)" }}>
                <span className="dot" style={{ background: s.hits_24h ? "var(--primary)" : "var(--ink-30)" }} />
                <div style={{ marginLeft: 12, flex: 1 }}>
                  <div className="t-mono" style={{ color: "var(--ink)" }}>{s.name}</div>
                  <div className="t-meta" style={{ marginTop: 2 }}>{s.description.slice(0, 60)}</div>
                </div>
                <span className="t-mono-sm" style={{ color: s.hits_24h ? "var(--primary)" : "var(--ink-60)" }}>{s.hits_24h} 命中</span>
              </div>
            ))}
      </div>
    </div>);
}

function MemoryPreview({ memory, onJump }) {
  const items = (memory.entries || []).slice(0, 4);
  const claude = memory.claudemd;
  return (
    <div className="card">
      <CardHeader
        title="记忆"
        sub={`memdir + CLAUDE.md`}
        right={<button className="btn btn-ghost btn-sm" onClick={() => onJump("memory")}>浏览 →</button>} />
      <div style={{ padding: 4 }}>
        {claude && claude.total_chars > 0 && (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 70px 60px", gap: 10, alignItems: "center", padding: "12px 16px", borderTop: "none" }}>
            <div className="t-mono" style={{ color: "var(--primary)" }}>CLAUDE.md</div>
            <span className="chip" style={{ justifySelf: "start" }}>常驻</span>
            <span className="t-mono-sm" style={{ color: "var(--ink-60)", textAlign: "right" }}>{API.fmtNumber(claude.total_chars)}</span>
          </div>
        )}
        {items.length === 0 && (!claude || !claude.total_chars)
          ? <div style={{ padding: "24px 16px", textAlign: "center", color: "var(--ink-60)" }} className="t-meta">暂无记忆</div>
          : items.map((m, i) => (
              <div key={m.file} style={{ display: "grid", gridTemplateColumns: "1fr 70px 60px", gap: 10, alignItems: "center", padding: "12px 16px", borderTop: "1px solid var(--divider-soft)" }}>
                <div className="t-mono" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.file}</div>
                <span className="chip" style={{ justifySelf: "start" }}>{m.scope}</span>
                <span className="t-mono-sm" style={{ color: "var(--ink-60)", textAlign: "right" }}>{API.fmtNumber(m.chars)}</span>
              </div>
            ))}
      </div>
    </div>);
}

Object.assign(window, { Overview });
