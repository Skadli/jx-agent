/* Permissions — 真实接 /api/permissions + /api/settings_json。 */

function Permissions({ onJump }) {
  const [mode, setMode]       = React.useState("rules");
  const [perm, setPerm]       = React.useState(null);
  const [sjson, setSjson]     = React.useState(null);
  const [editJson, setEditJson] = React.useState(null);

  const refresh = React.useCallback(async () => {
    const [p, s] = await Promise.all([API.get("/api/permissions"), API.get("/api/settings_json")]);
    if (!p.error) setPerm(p);
    if (!s.error) { setSjson(s); if (editJson === null) setEditJson(s.body); }
  }, [editJson]);

  React.useEffect(() => {
    refresh();
    const id = setInterval(refresh, 10000);
    return () => clearInterval(id);
  }, [refresh]);

  // 监听 hash 中的 #permissions?mode=json，便于从其他页跳过来
  React.useEffect(() => {
    const h = window.location.hash;
    if (h.includes("mode=json")) setMode("json");
  }, []);

  const setDefault = async (val) => {
    if (!perm) return;
    // 拼合并 settings.json
    const r = await API.put("/api/permissions/default_mode", { default_mode: val });
    if (r.error) { alert("保存失败：" + r.error); return; }
    refresh();
  };

  const addRule = async (group) => {
    const pattern = prompt(`输入 ${group} 规则 pattern（例：Bash(ls:*) 或 Read(./**)）：`);
    if (!pattern) return;
    const r = await API.post("/api/permissions/rule", { group, pattern });
    if (r.error) { alert("添加失败：" + r.error); return; }
    refresh();
  };

  const delRule = async (group, pattern) => {
    if (!confirm(`确定删除规则 ${group}: ${pattern}？`)) return;
    const r = await API.del("/api/permissions/rule", { group, pattern });
    if (r.error) { alert("删除失败：" + r.error); return; }
    refresh();
  };

  const saveJson = async () => {
    try {
      JSON.parse(editJson);
    } catch (e) {
      alert("JSON 不合法：" + e.message);
      return;
    }
    const r = await API.put("/api/settings_json", { body: editJson });
    if (r.error) { alert("保存失败：" + r.error); return; }
    alert("已保存");
    refresh();
  };

  const formatJson = () => {
    try {
      setEditJson(JSON.stringify(JSON.parse(editJson), null, 2));
    } catch (e) {
      alert("JSON 不合法：" + e.message);
    }
  };

  const validateJson = () => {
    try {
      JSON.parse(editJson);
      alert("JSON 合法 ✓");
    } catch (e) {
      alert("JSON 错误：" + e.message);
    }
  };

  if (!perm) return <div className="page-body"><div className="t-body">加载中…</div></div>;

  const allowList = (perm.allow || []).map(p => parseRule(p, "allow"));
  const denyList  = (perm.deny  || []).map(p => parseRule(p, "deny"));

  return (
    <div data-screen-label="07 权限">
      <PageHeader
        title="权限"
        sub={`settings.json · ${allowList.length + denyList.length} 条规则 · 默认: ${perm.default_mode} · 协议对齐 Claude Code`}
        actions={
          <>
            <Segmented value={mode} onChange={setMode} options={[
              { id: "rules", label: "规则视图" },
              { id: "json",  label: "settings.json" },
            ]} />
            {mode === "json" && (
              <button className="btn btn-primary" onClick={saveJson}><Icon name="check" size={13} color="#fff"/>保存配置</button>
            )}
          </>
        }
      />

      <div className="page-body">
        <div className="grid-4">
          <StatCard label="允许规则" value={allowList.length} sub="自动放行"        color="var(--success-fg)" />
          <StatCard label="询问规则" value={perm.default_mode === "ask" ? "ask" : "—"} sub="defaultMode" color="var(--primary)" />
          <StatCard label="拒绝规则" value={denyList.length} sub="直接挡掉"        color="var(--danger-fg)" />
          <StatCard label="24h 询问" value={perm.kpi.approved + perm.kpi.denied} sub={`批准 ${perm.kpi.approved} · 拒绝 ${perm.kpi.denied}`} />
        </div>

        <div className="card" style={{ marginTop: 16, padding: "12px 18px", display: "flex", alignItems: "center", gap: 16 }}>
          <div className="t-eyebrow" style={{ marginRight: 4 }}>默认模式</div>
          <Segmented value={perm.default_mode} onChange={setDefault} options={[
            { id: "allow", label: "全部允许" },
            { id: "ask",   label: "询问" },
            { id: "deny",  label: "默认拒绝" },
          ]} />
          {perm.default_mode === "allow" && (
            <span className="chip chip-warning"><Icon name="alert" size={11}/>注意：不建议生产环境使用</span>
          )}
          <div className="grow" />
          <span className="t-meta">{perm.source_paths && perm.source_paths.length ? perm.source_paths.join(" · ") : "未保存"}</span>
        </div>

        {mode === "rules" ? (
          <>
            <div className="grid-3" style={{ marginTop: 16 }}>
              <RuleColumn title="允许" desc="自动放行" tone="success" rules={allowList} onAdd={() => addRule("allow")} onDel={p => delRule("allow", p.raw)} />
              <RuleColumn title="询问" desc="defaultMode=ask 时" tone="primary" rules={[]} onAdd={() => {}} onDel={() => {}} />
              <RuleColumn title="拒绝" desc="直接挡掉" tone="danger" rules={denyList} onAdd={() => addRule("deny")} onDel={p => delRule("deny", p.raw)} />
            </div>

            <div style={{ marginTop: 16 }}>
              <PromptLog rows={perm.recent} />
            </div>
          </>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 320px", gap: 16, marginTop: 16 }}>
            <div className="card" style={{ overflow: "hidden" }}>
              <CardHeader
                title={sjson ? sjson.path : "settings.json"}
                right={
                  <>
                    <button className="btn btn-ghost btn-sm" onClick={validateJson}>校验</button>
                    <button className="btn btn-ghost btn-sm" onClick={formatJson}>格式化</button>
                    <button className="btn btn-secondary btn-sm" onClick={() => navigator.clipboard.writeText(editJson || "")}><Icon name="copy" size={13}/>复制</button>
                  </>
                }
              />
              <textarea
                value={editJson || ""}
                onChange={e => setEditJson(e.target.value)}
                spellCheck={false}
                style={{
                  margin: 0, padding: "20px 24px", border: 0, outline: "none",
                  fontFamily: "var(--font-mono)", fontSize: 12.5, lineHeight: 1.75,
                  color: "var(--ink)", whiteSpace: "pre",
                  background: "var(--canvas)", overflow: "auto",
                  width: "100%", maxHeight: "60vh", minHeight: "40vh", resize: "vertical",
                }} />
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              <div className="card">
                <CardHeader title="结构验证" />
                <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 8, paddingTop: 4 }}>
                  <ValidRow ok={isJSON(editJson)} label="JSON 语法" v={isJSON(editJson) ? "通过" : "错误"} />
                  <ValidRow ok={true} label="defaultMode" v={perm.default_mode} />
                  <ValidRow ok={true} label="规则总数" v={`${allowList.length + denyList.length} 条`} />
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function parseRule(raw, group) {
  // "Bash(ls:*)" → {tool:"Bash", scope:"ls:*", raw, group}
  const m = raw.match(/^([A-Za-z][A-Za-z0-9_]*)(?:\(([^)]*)\))?$/);
  if (!m) return { tool: raw, scope: "", raw, group };
  return { tool: m[1], scope: m[2] || "*", raw, group };
}

function isJSON(s) {
  try { JSON.parse(s); return true; } catch { return false; }
}

function RuleColumn({ title, desc, tone, rules, onAdd, onDel }) {
  const color = tone === "success" ? "var(--success-fg)" : tone === "primary" ? "var(--primary)" : "var(--danger-fg)";
  const bg    = tone === "success" ? "var(--success-bg)" : tone === "primary" ? "var(--primary-soft)" : "var(--danger-bg)";
  return (
    <div className="card">
      <div className="card-header" style={{ borderBottom: 0, padding: "16px 20px 0" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, background: color }} />
            <span className="t-card-title">{title}</span>
            <span className="chip" style={{ background: bg, color, fontWeight: 500 }}>{rules.length}</span>
          </div>
          <div className="t-meta" style={{ marginTop: 4 }}>{desc}</div>
        </div>
        <button className="btn-icon" onClick={onAdd}><Icon name="plus" size={14}/></button>
      </div>
      <div style={{ padding: "12px 16px 16px", display: "flex", flexDirection: "column", gap: 8 }}>
        {rules.length === 0 && <div className="t-meta" style={{ padding: "16px 0", textAlign: "center" }}>暂无规则</div>}
        {rules.map((r, i) => (
          <div key={i} style={{
            padding: "10px 12px", background: "var(--pearl)",
            border: "1px solid var(--hairline)", borderRadius: 8,
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
              <span className="t-mono-strong" style={{ color: "var(--ink)" }}>{r.tool}</span>
              <button className="btn-icon" style={{ width: 22, height: 22 }} onClick={() => onDel(r)}><Icon name="trash" size={12} color="var(--danger)"/></button>
            </div>
            <div className="t-mono-sm" style={{ color, marginTop: 6, wordBreak: "break-all" }}>{r.scope}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function PromptLog({ rows }) {
  return (
    <div className="card">
      <CardHeader
        title="询问日志"
        sub={`近 ${rows.length} 次决策`}
        right={
          <button className="btn btn-secondary btn-sm" onClick={() => {
            API.download("permission_decisions.jsonl", rows.map(r => JSON.stringify(r)).join("\n"));
          }}><Icon name="download" size={13}/>JSONL</button>
        }
      />
      {rows.length === 0 ? (
        <div style={{ padding: 32, textAlign: "center", color: "var(--ink-60)" }} className="t-meta">暂无决策</div>
      ) : (
      <table className="tbl">
        <thead>
          <tr>
            <th style={{ width: 100 }}>时间</th>
            <th style={{ width: 140 }}>工具</th>
            <th>pattern</th>
            <th style={{ width: 160 }}>会话</th>
            <th style={{ width: 100 }}>scope</th>
            <th style={{ width: 110 }}>结果</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p, i) => (
            <tr key={i}>
              <td className="t-mono-sm" style={{ color: "var(--ink-60)" }}>{API.relTime(p.ts)}</td>
              <td><span className="t-mono">{p.tool_name}</span></td>
              <td className="t-mono-sm" style={{ color: "var(--ink-80)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 360 }}>{p.pattern || "—"}</td>
              <td className="t-mono-sm" style={{ color: "var(--ink-60)" }}>{(p.session_id || "").slice(0, 16)}</td>
              <td className="t-row" style={{ color: "var(--ink-80)" }}>{p.scope}</td>
              <td>
                {p.decision === "allow"
                  ? <span className="chip chip-success"><Icon name="check" size={11}/>已允许</span>
                  : <span className="chip chip-danger"><Icon name="x" size={11}/>已拒绝</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      )}
    </div>
  );
}

function ValidRow({ ok, label, v }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
        <Icon name="check" size={13} color={ok ? "var(--success)" : "var(--danger)"} />
        <span className="t-row" style={{ color: "var(--ink)" }}>{label}</span>
      </span>
      <span className="t-mono-sm" style={{ color: "var(--ink-60)" }}>{v}</span>
    </div>
  );
}

Object.assign(window, { Permissions });
