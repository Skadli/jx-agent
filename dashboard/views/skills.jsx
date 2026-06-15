/* Skills — admin table。真实接 /api/skills。
   点行/卡片 → 打开右侧 Drawer，含三 tab：概览 / 画布 / 源码。 */

function Skills({ onJump }) {
  const [view, setView]         = React.useState("table");
  const [skills, setSkills]     = React.useState([]);
  const [drawerSkill, setDrawerSkill] = React.useState(null);
  const [tab, setTab]           = React.useState("overview");

  const refresh = React.useCallback(async () => {
    const r = await API.get("/api/skills");
    if (!r.error) setSkills(r.skills || []);
  }, []);

  React.useEffect(() => {
    refresh();
    const id = setInterval(refresh, 30000);
    return () => clearInterval(id);
  }, [refresh]);

  const hits24 = skills.reduce((a, s) => a + s.hits_24h, 0);
  const hits7d = skills.reduce((a, s) => a + s.hits_7d, 0);
  const totalChars = skills.reduce((a, s) => a + s.chars, 0);

  const reload = async () => {
    const r = await API.post("/api/skills/reload");
    if (r.error) alert("重载失败：" + r.error);
    else { alert("已重新扫描"); refresh(); }
  };

  const pick = (s) => { setDrawerSkill(s); setTab("overview"); };

  return (
    <div data-screen-label="05 技能">
      <PageHeader
        title="技能"
        sub={`${skills.length} 个已注册 · 关键词匹配 · 协议: SKILL.md`}
        actions={
          <>
            <Segmented value={view} onChange={setView} options={[
              { id: "table", label: "表格" },
              { id: "grid",  label: "卡片" },
            ]} />
            <button className="btn btn-secondary" onClick={reload}><Icon name="refresh" size={13}/>重新扫描</button>
            <button className="btn btn-primary" onClick={() => {
              alert("新建 skill：请在 skills/ 下新建一个目录，放入带 frontmatter 的 SKILL.md，然后点'重新扫描'");
            }}><Icon name="plus" size={13} color="#fff"/>新建技能</button>
          </>
        }
      />

      <div className="page-body">
        <div className="grid-4">
          <StatCard label="已注册" value={skills.length} sub={`${API.fmtNumber(totalChars)} 字`} />
          <StatCard label="24h 命中" value={hits24} sub={hits24 > 0 ? "活跃" : "无"} />
          <StatCard label="7d 命中" value={hits7d} sub="" />
          <StatCard label="目录" value={skills.length > 0 ? "./skills" : "—"} sub="协议: SKILL.md" />
        </div>

        <div style={{ marginTop: 16 }}>
          {view === "table" ? (
            <div className="card">
              <CardHeader
                title="已注册 skill"
                sub="点击行查看可视化结构"
                right={
                  <div className="search-wrap" style={{ width: 200 }}>
                    <span className="search-icon"><Icon name="search" size={13} color="var(--ink-48)"/></span>
                    <input className="search" placeholder="搜索"/>
                  </div>
                } />
              {skills.length === 0 ? (
                <div style={{ padding: 32, textAlign: "center", color: "var(--ink-60)" }} className="t-meta">暂无 skill</div>
              ) : (
                <table className="tbl">
                  <thead>
                    <tr>
                      <th style={{ width: 36 }}></th>
                      <th>Skill</th>
                      <th style={{ width: 240 }}>关键词</th>
                      <th style={{ width: 80, textAlign: "right" }}>24h</th>
                      <th style={{ width: 80, textAlign: "right" }}>7d</th>
                      <th style={{ width: 36 }}></th>
                    </tr>
                  </thead>
                  <tbody>
                    {skills.map(s => (
                      <tr key={s.id} onClick={() => pick(s)} style={{ cursor: "pointer", background: drawerSkill && drawerSkill.id === s.id ? "var(--primary-soft)" : "transparent" }}>
                        <td><span className={`dot ${s.hits_24h > 0 ? "dot-up" : "dot-off"}`} /></td>
                        <td>
                          <div className="t-mono-strong" style={{ color: drawerSkill && drawerSkill.id === s.id ? "var(--primary)" : "var(--ink)" }}>{s.name}</div>
                          <div className="t-meta" style={{ marginTop: 3 }}>{s.description.slice(0, 80)}</div>
                        </td>
                        <td>
                          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                            {(s.keywords || []).slice(0, 4).map(t => <span key={t} className="chip" style={{ fontSize: 10.5 }}>{t}</span>)}
                            {(s.keywords || []).length > 4 && <span className="t-meta">+{s.keywords.length - 4}</span>}
                          </div>
                        </td>
                        <td className="col-num" style={{ color: s.hits_24h ? "var(--primary)" : "var(--ink-60)" }}>{s.hits_24h}</td>
                        <td className="col-num">{s.hits_7d}</td>
                        <td><button className="btn-icon" onClick={(e) => { e.stopPropagation(); pick(s); }}><Icon name="chevron-r" size={14}/></button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          ) : (
            <div className="grid-3">
              {skills.map(s => <SkillCard key={s.id} s={s} onClick={() => pick(s)} />)}
            </div>
          )}
        </div>
      </div>

      <Drawer
        open={!!drawerSkill}
        onClose={() => setDrawerSkill(null)}
        storageKey="skill-canvas-drawer"
        title={drawerSkill ? drawerSkill.name : ""}
        subtitle={drawerSkill ? drawerSkill.description.slice(0, 80) : ""}
        actions={
          <Segmented value={tab} onChange={setTab} options={[
            { id: "overview", label: "概览" },
            { id: "canvas",   label: "画布" },
            { id: "source",   label: "源码" },
          ]} />
        }
      >
        {drawerSkill && tab === "overview" && <SkillOverview s={drawerSkill} />}
        {drawerSkill && tab === "canvas"   && (
          <div style={{ height: "100%", minHeight: 480 }}>
            <SkillCanvas skillId={drawerSkill.id} />
          </div>
        )}
        {drawerSkill && tab === "source"   && <SkillSource skillId={drawerSkill.id} />}
      </Drawer>
    </div>
  );
}


function SkillOverview({ s }) {
  const keywords = s.keywords || [];
  const sourceDir = (s.source || "").split(/[\\/]/).slice(-2).join("/") || "—";
  const structureFile = (s.structure || "").split(/[\\/]/).slice(-2).join("/") || `${s.id}/structure.json`;

  return (
    <div style={{ padding: "18px 20px" }}>
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(96px, 1fr))",
        gap: 8,
        marginBottom: 20,
      }}>
        <SkillOverviewStat label="24h 命中" value={s.hits_24h} accent={s.hits_24h ? "var(--primary)" : undefined} />
        <SkillOverviewStat label="7d 命中" value={s.hits_7d} />
        <SkillOverviewStat label="字数" value={API.fmtNumber(s.chars)} sub="SKILL.md" />
      </div>

      <div className="t-eyebrow" style={{ marginBottom: 10 }}>文件</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 20 }}>
        <KV k="Skill"    v={sourceDir} />
        <KV k="结构"     v={structureFile} />
        <KV k="协议"     v="SKILL.md + structure.json" mono={false} />
        <KV k="优先级"   v={s.priority === 0 ? "project" : s.priority === 1 ? "global" : "repo"} />
      </div>

      <div className="t-eyebrow" style={{ marginBottom: 10 }}>触发词</div>
      {keywords.length ? (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {keywords.map(t => <span key={t} className="chip">{t}</span>)}
        </div>
      ) : (
        <div className="t-meta">未配置关键词</div>
      )}
    </div>
  );
}

function SkillOverviewStat({ label, value, sub, accent }) {
  return (
    <div style={{
      padding: "12px 14px",
      background: "var(--pearl)",
      border: "1px solid var(--hairline)",
      borderRadius: "var(--r-md)",
      minWidth: 0,
    }}>
      <div className="t-eyebrow">{label}</div>
      <div className="t-stat-sm" style={{ marginTop: 7, color: accent || "var(--ink)" }}>{value}</div>
      {sub && <div className="t-meta" style={{ marginTop: 4 }}>{sub}</div>}
    </div>
  );
}


function SkillSource({ skillId }) {
  const [data, setData] = React.useState(null);
  const [err, setErr]   = React.useState(null);
  React.useEffect(() => {
    if (!skillId) return;
    setData(null); setErr(null);
    // 源码读 SKILL.md 正文（/source），与画布(/structure)解耦——画布没生成也能看源码
    API.get(`/api/skills/${encodeURIComponent(skillId)}/source`).then(r => {
      if (r.error) setErr(r.error);
      else setData(r);
    });
  }, [skillId]);
  if (err)  return <div style={{ padding: 40, textAlign: "center", color: "var(--danger)" }}>加载失败：{err}</div>;
  if (!data) return <div style={{ padding: 40, textAlign: "center", color: "var(--ink-60)" }}>加载中…</div>;
  return (
    <pre style={{
      margin: 0, padding: "16px 20px",
      fontFamily: "var(--font-mono)", fontSize: 11.5, lineHeight: 1.65,
      color: "var(--ink-80)", whiteSpace: "pre-wrap",
      background: "var(--canvas)",
      minHeight: "100%",
    }}>{data.body || "(空)"}</pre>
  );
}


function SkillCard({ s, onClick }) {
  return (
    <div className="card" onClick={onClick} style={{ cursor: "pointer" }}>
      <div className="card-padded" style={{ paddingBottom: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <div className="t-mono-strong">{s.name}</div>
            <div className="t-card-title" style={{ marginTop: 4 }}>{s.description.slice(0, 30)}</div>
          </div>
          <span className="chip chip-success chip-dot">已加载</span>
        </div>
        <p className="t-body" style={{ margin: "12px 0 0", minHeight: 60 }}>{s.description}</p>
      </div>
      <div style={{ padding: "12px 20px", borderTop: "1px solid var(--hairline)", background: "var(--pearl)" }}>
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 12 }}>
          {(s.keywords || []).slice(0, 6).map(t => <span key={t} className="chip" style={{ fontSize: 10.5 }}>{t}</span>)}
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span className="t-meta">{API.fmtNumber(s.chars)} 字</span>
          <span className="t-mono-sm" style={{ color: s.hits_24h ? "var(--primary)" : "var(--ink-60)" }}>{s.hits_24h} 命中 / 24h</span>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { Skills });
