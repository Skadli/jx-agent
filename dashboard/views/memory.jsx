/* Memory — memdir + CLAUDE.md. 真实接 /api/memory。 */

function Memory({ onJump }) {
  const [entries, setEntries] = React.useState([]);
  const [claude, setClaude]   = React.useState(null);
  const [active, setActive]   = React.useState("__claudemd__");
  const [body, setBody]       = React.useState("");
  const [editBuf, setEditBuf] = React.useState("");
  const [scope, setScope]     = React.useState("all");
  const [mode, setMode]       = React.useState("preview");  // preview | edit
  const [showNew, setShowNew] = React.useState(false);

  const refresh = React.useCallback(async () => {
    const r = await API.get("/api/memory");
    if (!r.error) {
      setEntries(r.entries || []);
      setClaude(r.claudemd);
    }
  }, []);

  React.useEffect(() => {
    refresh();
    const id = setInterval(refresh, 30000);
    return () => clearInterval(id);
  }, [refresh]);

  // 拉单文件
  React.useEffect(() => {
    if (!active) return;
    let alive = true;
    (async () => {
      const path = active === "__claudemd__" ? "__claudemd__" : encodeURIComponent(active);
      const r = await API.get(`/api/memory/${path}`);
      if (alive && !r.error) { setBody(r.body || ""); setEditBuf(r.body || ""); }
    })();
    return () => { alive = false; };
  }, [active]);

  const isClaude = active === "__claudemd__";
  const file = entries.find(m => m.file === active);
  const filtered = scope === "all" ? entries : entries.filter(m => m.scope === scope);
  const totalChars = (claude ? claude.total_chars : 0) + entries.reduce((a, e) => a + e.chars, 0);

  const save = async () => {
    if (isClaude) return;
    const r = await API.put(`/api/memory/${encodeURIComponent(active)}`, { body: editBuf });
    if (r.error) { alert("保存失败：" + r.error); return; }
    setBody(editBuf);
    setMode("preview");
    refresh();
  };

  const del = async () => {
    if (isClaude || !file) return;
    if (!confirm(`确定删除记忆 ${file.file}？此操作不可恢复。`)) return;
    const r = await API.del(`/api/memory/${encodeURIComponent(active)}`);
    if (r.error) { alert("删除失败：" + r.error); return; }
    setActive("__claudemd__");
    refresh();
  };

  return (
    <div data-screen-label="04 记忆" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <PageHeader
        title="记忆"
        sub={`CLAUDE.md + memdir/ · ${API.fmtNumber(totalChars)} 字 · ${entries.length} 条`}
        actions={
          <>
            <button className="btn btn-secondary" onClick={() => {
              API.download("memory-export.md", body);
            }}><Icon name="download" size={13}/>导出当前</button>
            <button className="btn btn-primary" onClick={() => setShowNew(true)}><Icon name="plus" size={13} color="#fff"/>新建记忆</button>
          </>
        }
      />

      <div style={{
        display: "grid",
        gridTemplateColumns: "320px 1fr 320px",
        gap: 16, padding: "16px 28px 24px", flex: 1, minHeight: 0,
      }}>
        {/* LEFT */}
        <div className="card" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--hairline)" }}>
            <div className="search-wrap">
              <span className="search-icon"><Icon name="search" size={13} color="var(--ink-48)"/></span>
              <input className="search" placeholder="搜索记忆"/>
            </div>
            <div style={{ display: "flex", gap: 4, marginTop: 10, flexWrap: "wrap" }}>
              {[
                ["all",       "全部",      entries.length],
                ["user",      "user",      entries.filter(m => m.scope === "user").length],
                ["feedback",  "feedback",  entries.filter(m => m.scope === "feedback").length],
                ["project",   "project",   entries.filter(m => m.scope === "project").length],
                ["reference", "reference", entries.filter(m => m.scope === "reference").length],
              ].map(([id, label, n]) => (
                <button key={id} onClick={() => setScope(id)}
                  className="chip"
                  style={{
                    border: 0, cursor: "pointer",
                    background: scope === id ? "var(--ink)" : "rgba(0,0,0,0.05)",
                    color: scope === id ? "#fff" : "var(--ink-80)",
                  }}>{label} · {n}</button>
              ))}
            </div>
          </div>

          <div style={{ overflowY: "auto", flex: 1 }}>
            {claude && (
              <div onClick={() => setActive("__claudemd__")} style={{
                padding: "12px 14px",
                borderLeft: isClaude ? "3px solid var(--primary)" : "3px solid transparent",
                borderBottom: "1px solid var(--divider-soft)",
                background: isClaude ? "var(--primary-soft)" : "transparent",
                cursor: "pointer",
              }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <Icon name="stack" size={13} color={isClaude ? "var(--primary)" : "var(--ink-60)"}/>
                    <span className="t-mono-strong" style={{ color: isClaude ? "var(--primary)" : "var(--ink)" }}>CLAUDE.md</span>
                  </span>
                  <span className="chip chip-info">常驻</span>
                </div>
                <div className="t-meta" style={{ marginTop: 6 }}>项目+全局 · {API.fmtNumber(claude.total_chars)} 字</div>
              </div>
            )}

            {filtered.length === 0 && !claude && (
              <div style={{ padding: 32, textAlign: "center", color: "var(--ink-60)" }} className="t-meta">暂无记忆<br/>点击"新建记忆"</div>
            )}

            {filtered.map(m => (
              <MemoryFileItem key={m.file} m={m} active={active === m.file} onClick={() => setActive(m.file)} />
            ))}
          </div>
        </div>

        {/* CENTER */}
        <div className="card" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <div className="card-header">
            <div>
              <div className="t-mono-sm" style={{ color: "var(--ink-60)" }}>{isClaude ? "CLAUDE.md" : `memdir/${file ? file.file : ""}`}</div>
              <div className="t-card-title" style={{ marginTop: 3 }}>
                {isClaude ? "项目记忆（顶部注入）" : (file ? `${file.scope} · ${API.fmtNumber(file.chars)} 字` : "")}
              </div>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn btn-ghost btn-sm" onClick={() => { navigator.clipboard.writeText(body); }}><Icon name="copy" size={13}/>复制</button>
              {!isClaude && mode !== "edit" && <button className="btn btn-secondary btn-sm" onClick={() => setMode("edit")}><Icon name="edit" size={13}/>编辑</button>}
              {mode === "edit" && (
                <>
                  <button className="btn btn-ghost btn-sm" onClick={() => { setEditBuf(body); setMode("preview"); }}>取消</button>
                  <button className="btn btn-primary btn-sm" onClick={save}><Icon name="check" size={13} color="#fff"/>保存</button>
                </>
              )}
              {!isClaude && file && <button className="btn-icon" title="删除" onClick={del}><Icon name="trash" size={14} color="var(--danger)"/></button>}
            </div>
          </div>

          {mode === "edit" ? (
            <textarea
              value={editBuf}
              onChange={e => setEditBuf(e.target.value)}
              style={{
                margin: 0, padding: "24px 32px", border: "none", outline: "none", resize: "none",
                fontFamily: "var(--font-mono)", fontSize: 12.5, lineHeight: 1.75,
                color: "var(--ink-80)", background: "var(--canvas)", flex: 1,
              }} />
          ) : (
            <pre style={{
              margin: 0, padding: "24px 32px",
              fontFamily: "var(--font-mono)", fontSize: 12.5, lineHeight: 1.75,
              color: "var(--ink-80)", whiteSpace: "pre-wrap",
              background: "var(--canvas)", flex: 1, overflowY: "auto",
            }}>{body || "—"}</pre>
          )}
        </div>

        {/* RIGHT */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16, overflow: "auto" }}>
          <div className="card">
            <CardHeader title="元信息" />
            <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <KV k="路径"    v={isClaude ? "CLAUDE.md" : (file ? `memdir/${file.file}` : "—")} />
              <KV k="scope"   v={isClaude ? "project (顶部)" : (file ? file.scope : "—")} />
              <KV k="字数"    v={isClaude ? API.fmtNumber(claude ? claude.total_chars : 0) : (file ? API.fmtNumber(file.chars) : "—")} />
              <KV k="最近修改" v={file ? API.relTime(file.mtime * 1000) : "—"} />
            </div>
          </div>
        </div>
      </div>

      {showNew && <NewMemoryModal onClose={() => { setShowNew(false); refresh(); }} />}
    </div>
  );
}

function NewMemoryModal({ onClose }) {
  const [name, setName] = React.useState("");
  const [desc, setDesc] = React.useState("");
  const [type, setType] = React.useState("user");
  const [body, setBody] = React.useState("");
  const [saving, setSaving] = React.useState(false);

  const submit = async () => {
    if (!name.trim() || !desc.trim()) { alert("name/description 不能为空"); return; }
    setSaving(true);
    const r = await API.post("/api/memory", { name, description: desc, type, body });
    setSaving(false);
    if (r.error) { alert("创建失败：" + r.error); return; }
    onClose();
  };

  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.4)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100,
    }} onClick={onClose}>
      <div className="card" style={{ width: 480, padding: 0 }} onClick={e => e.stopPropagation()}>
        <CardHeader title="新建记忆" right={<button className="btn-icon" onClick={onClose}><Icon name="x" size={14}/></button>} />
        <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div>
            <div className="t-row-strong" style={{ marginBottom: 6 }}>名称</div>
            <input className="field" value={name} onChange={e => setName(e.target.value)} placeholder="例如 preferred_format" />
          </div>
          <div>
            <div className="t-row-strong" style={{ marginBottom: 6 }}>描述</div>
            <input className="field" value={desc} onChange={e => setDesc(e.target.value)} placeholder="一句话说清楚是什么" />
          </div>
          <div>
            <div className="t-row-strong" style={{ marginBottom: 6 }}>类型</div>
            <Segmented value={type} onChange={setType} options={[
              { id: "user",      label: "user" },
              { id: "feedback",  label: "feedback" },
              { id: "project",   label: "project" },
              { id: "reference", label: "reference" },
            ]} />
          </div>
          <div>
            <div className="t-row-strong" style={{ marginBottom: 6 }}>正文</div>
            <textarea className="field" rows={6} value={body} onChange={e => setBody(e.target.value)} placeholder="markdown 正文" style={{ fontFamily: "var(--font-mono)" }}/>
          </div>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 6 }}>
            <button className="btn btn-ghost btn-sm" onClick={onClose}>取消</button>
            <button className="btn btn-primary btn-sm" disabled={saving} onClick={submit}>{saving ? "保存中…" : "创建"}</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function MemoryFileItem({ m, active, onClick }) {
  const scopeColors = {
    user:      { bg: "rgba(0,102,204,0.10)",  fg: "var(--primary)" },
    feedback:  { bg: "rgba(242,180,65,0.16)", fg: "var(--warning-fg)" },
    project:   { bg: "rgba(48,162,114,0.10)", fg: "var(--success-fg)" },
    reference: { bg: "rgba(193,60,123,0.10)", fg: "#9c2f5f" },
  };
  const sc = scopeColors[m.scope] || scopeColors.user;
  return (
    <div onClick={onClick} style={{
      padding: "12px 14px",
      borderLeft: active ? "3px solid var(--primary)" : "3px solid transparent",
      borderBottom: "1px solid var(--divider-soft)",
      background: active ? "var(--primary-soft)" : "transparent",
      cursor: "pointer",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span className="t-mono-sm" style={{ color: active ? "var(--primary)" : "var(--ink)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.file}</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 6 }}>
        <span className="chip" style={{ background: sc.bg, color: sc.fg, fontSize: 10.5 }}>{m.scope}</span>
        <span className="t-meta">{API.fmtNumber(m.chars)} 字</span>
        <span className="t-meta" style={{ marginLeft: "auto" }}>{API.relTime(m.mtime * 1000)}</span>
      </div>
      <div className="t-meta" style={{ marginTop: 4 }}>{m.description}</div>
    </div>
  );
}

Object.assign(window, { Memory });
