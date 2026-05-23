/* Persona — file tree + editor + inspector. 真实读 /api/persona。 */

function Persona({ onJump }) {
  const [files, setFiles]     = React.useState([]);
  const [active, setActive]   = React.useState(null);
  const [body, setBody]       = React.useState("");
  const [editBuf, setEditBuf] = React.useState("");
  const [mode, setMode]       = React.useState("preview");  // preview / source / edit
  const [saving, setSaving]   = React.useState(false);

  // 拉文件列表
  const refreshList = React.useCallback(async () => {
    const r = await API.get("/api/persona");
    if (!r.error) {
      setFiles(r.files || []);
      if (!active && r.files && r.files.length > 0) {
        const def = r.files.find(f => f.name === "style.md") || r.files[0];
        setActive(def.name);
      }
    }
  }, [active]);

  React.useEffect(() => {
    refreshList();
    const id = setInterval(refreshList, 30000);
    return () => clearInterval(id);
  }, [refreshList]);

  // 拉单文件内容
  React.useEffect(() => {
    if (!active) return;
    let alive = true;
    (async () => {
      const r = await API.get(`/api/persona/${encodeURIComponent(active)}`);
      if (alive && !r.error) { setBody(r.body || ""); setEditBuf(r.body || ""); }
    })();
    return () => { alive = false; };
  }, [active]);

  const file = files.find(f => f.name === active);
  const totalChars = files.reduce((acc, f) => acc + f.chars, 0);

  const save = async () => {
    setSaving(true);
    const r = await API.put(`/api/persona/${encodeURIComponent(active)}`, { body: editBuf });
    setSaving(false);
    if (r.error) { alert("保存失败：" + r.error); return; }
    setBody(editBuf);
    setMode("preview");
    refreshList();
  };

  const reload = async () => {
    const r = await API.post("/api/instance/reload");
    if (r.error) alert("重载失败：" + r.error);
    else { alert("已重载"); refreshList(); }
  };

  const exportPrompt = () => {
    // 顺序拼装 5 份 md
    const order = ["root.md", "personality.md", "beliefs.md", "style.md", "examples.md"];
    Promise.all(order.map(n => API.get(`/api/persona/${encodeURIComponent(n)}`))).then(results => {
      const parts = results.filter(r => !r.error).map(r => r.body);
      API.download("persona-prompt.md", parts.join("\n\n---\n\n"));
    });
  };

  return (
    <div data-screen-label="03 人设" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <PageHeader
        title="人设"
        sub={`persona/ · ${API.fmtNumber(totalChars)} 字 · ${files.length} 份 · 监听 5 秒轮询`}
        actions={
          <>
            <span className="chip chip-success chip-dot">已加载</span>
            <button className="btn btn-secondary" onClick={exportPrompt}><Icon name="download" size={13}/>导出 prompt</button>
            <button className="btn btn-secondary" onClick={reload}><Icon name="refresh" size={13}/>手动重载</button>
          </>
        }
      />

      <div style={{
        display: "grid",
        gridTemplateColumns: "300px 1fr 320px",
        gap: 16,
        padding: "16px 28px 24px",
        flex: 1, minHeight: 0, alignItems: "stretch",
      }}>
        {/* LEFT */}
        <div className="card" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <CardHeader title="persona/" sub={`${files.length} 份 markdown`} />
          <div style={{ flex: 1, overflowY: "auto" }}>
            {files.map(f => (
              <PersonaFileItem key={f.name} f={f} active={active === f.name} onClick={() => setActive(f.name)} />
            ))}
          </div>
          <div style={{ padding: "10px 14px", borderTop: "1px solid var(--hairline)", background: "var(--pearl)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <span className="t-meta" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <span className="dot dot-up" /> 监听中
            </span>
            <span className="t-mono-sm" style={{ color: "var(--ink-60)" }}>5s 轮询</span>
          </div>
        </div>

        {/* CENTER */}
        <div className="card" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <div className="card-header">
            <div>
              <div className="t-mono-sm" style={{ color: "var(--ink-60)" }}>persona/{active || "—"}</div>
              <div className="t-card-title" style={{ marginTop: 3 }}>{file ? `${API.fmtNumber(file.chars)} 字` : ""}</div>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <Segmented value={mode === "edit" ? "source" : mode} onChange={setMode} options={[
                { id: "preview", label: "预览" },
                { id: "source",  label: "源码" },
              ]} />
              {mode !== "edit" ? (
                <button className="btn btn-secondary btn-sm" onClick={() => setMode("edit")}><Icon name="edit" size={13}/>编辑</button>
              ) : (
                <>
                  <button className="btn btn-ghost btn-sm" onClick={() => { setEditBuf(body); setMode("preview"); }}>取消</button>
                  <button className="btn btn-primary btn-sm" disabled={saving} onClick={save}><Icon name="check" size={13} color="#fff"/>{saving ? "保存中…" : "保存"}</button>
                </>
              )}
            </div>
          </div>

          {mode === "edit" ? (
            <textarea
              value={editBuf}
              onChange={e => setEditBuf(e.target.value)}
              style={{
                margin: 0, padding: "20px 28px", border: "none", outline: "none", resize: "none",
                fontFamily: "var(--font-mono)", fontSize: 12.5, lineHeight: 1.75,
                color: "var(--ink-80)", background: "var(--canvas)", flex: 1,
              }}
            />
          ) : (
            <pre style={{
              margin: 0, padding: "20px 28px",
              fontFamily: "var(--font-mono)", fontSize: 12.5, lineHeight: 1.75,
              color: "var(--ink-80)", whiteSpace: "pre-wrap",
              background: mode === "source" ? "var(--pearl)" : "var(--canvas)",
              flex: 1, overflowY: "auto",
            }}>{body || "加载中…"}</pre>
          )}

          <div style={{ padding: "8px 16px", borderTop: "1px solid var(--hairline)", background: "var(--pearl)", display: "flex", justifyContent: "space-between" }}>
            <span className="t-meta">UTF-8 · LF · {file ? file.chars : 0} 字</span>
            <span className="t-meta">{mode === "edit" ? "编辑模式" : "只读"}</span>
          </div>
        </div>

        {/* RIGHT */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16, overflow: "auto" }}>
          <BudgetInjectionCard files={files} totalChars={totalChars} />
          <BannedCard />
        </div>
      </div>
    </div>
  );
}

function PersonaFileItem({ f, active, onClick }) {
  return (
    <div onClick={onClick} style={{
      padding: "12px 14px",
      borderLeft: active ? "3px solid var(--primary)" : "3px solid transparent",
      borderBottom: "1px solid var(--divider-soft)",
      background: active ? "var(--primary-soft)" : "transparent",
      cursor: "pointer",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span className="t-mono-strong" style={{ color: active ? "var(--primary)" : "var(--ink)" }}>{f.name}</span>
        <span className="t-mono-sm" style={{ color: "var(--ink-60)" }}>{API.fmtNumber(f.chars)}</span>
      </div>
      <div className="t-meta" style={{ marginTop: 4 }}>{f.summary}</div>
      <div className="t-meta" style={{ marginTop: 4, color: "var(--ink-48)" }}>{API.relTime(f.mtime * 1000)}</div>
    </div>
  );
}

function BudgetInjectionCard({ files, totalChars }) {
  return (
    <div className="card">
      <CardHeader title="Prompt 注入" sub="persona 各 md 占比" />
      <div className="card-body">
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 14 }}>
          <span className="t-stat-sm">{API.fmtNumber(totalChars)}</span>
          <span className="t-mono-sm" style={{ color: "var(--ink-60)" }}>字</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {files.map(f => (
            <div key={f.name}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span className="t-row" style={{ color: "var(--ink)" }}>{f.name}</span>
                <span className="t-mono-sm" style={{ color: "var(--ink-60)" }}>{API.fmtNumber(f.chars)}</span>
              </div>
              <div style={{ marginTop: 4 }}>
                <Meter value={totalChars > 0 ? (f.chars / totalChars) * 100 : 0} max={100} color="var(--primary)" height={3} />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function BannedCard() {
  const words = [
    ["作为一个 AI", 0],
    ["您",         0],
    ["希望对你有帮助", 0],
    ["让我们一起",   0],
  ];
  return (
    <div className="card">
      <CardHeader title="禁词检测" sub="近 24h 输出内命中" />
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {words.map(([w, hits]) => (
          <div key={w} style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span className="t-mono-sm" style={{ color: "var(--ink)" }}>{w}</span>
            <span className={`chip ${hits === 0 ? "chip-success" : "chip-warning"}`}>
              {hits === 0 ? <><Icon name="check" size={11}/>0 次</> : `⚠ ${hits} 次`}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

Object.assign(window, { Persona });
