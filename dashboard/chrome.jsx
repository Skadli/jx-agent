/* Admin chrome — top bar (52px) + left rail (240px).
 * Pure white surfaces, hairline borders, single blue accent on active rail item.
 */

function TopBar({ active, onJump, onLogout, onToggleRail, railCollapsed }) {
  const [overview, setOverview] = React.useState(null);
  const [health, setHealth] = React.useState(null);
  const [menuOpen, setMenuOpen] = React.useState(false);

  React.useEffect(() => {
    let alive = true;
    const refresh = async () => {
      const [o, h] = await Promise.all([API.get("/api/overview"), API.get("/api/health")]);
      if (!alive) return;
      if (!o.error) setOverview(o);
      if (!h.error) setHealth(h);
    };
    refresh();
    const id = setInterval(refresh, 10000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const comp = (health && health.components) || {};
  const allOk = Object.values(comp).every(v => v === "up" || v === "disabled") || !health;
  const model = (overview && overview.model) || "—";

  return (
    <div className="topbar">
      <button
        className="btn-icon"
        title={railCollapsed ? "展开侧栏" : "收起侧栏"}
        onClick={onToggleRail}
        style={{ marginRight: 2 }}>
        <Icon name={railCollapsed ? "menu" : "menu-collapse"} size={16} />
      </button>
      <div className="wordmark" onClick={() => onJump("overview")} style={{ cursor: "pointer" }}>
        <Icon name="brand" size={18} color="var(--ink)" />
        <span>三十六贱<span className="accent">笑</span></span>
      </div>
      <span className="t-mono-sm" style={{ color: "var(--ink-48)", marginLeft: -4 }}>v1.0.0</span>

      <div style={{ height: 14, width: 1, background: "var(--hairline)" }} />

      <div className="crumb">
        <span>后台管理</span>
        <span className="sep">/</span>
        <span className="cur">{TAB_LABEL[active] || active}</span>
      </div>

      <div className="grow" />

      <div className="search-wrap" style={{ width: "clamp(140px, 24vw, 280px)" }}>
        <span className="search-icon"><Icon name="search" size={14} color="var(--ink-48)" /></span>
        <input className="search" placeholder="搜索会话 / 文件 / 命令  ⌘K" />
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
        <span className={`dot ${allOk ? "dot-up" : "dot-warn"}`} />
        <span className="t-meta" style={{ color: "var(--ink-80)" }}>{allOk ? "所有通道正常" : "通道需检查"}</span>
      </div>

      <div style={{ height: 14, width: 1, background: "var(--hairline)" }} />

      <span className="chip chip-mono" title="当前模型">
        {model}
      </span>

      <button className="btn-icon" title="设置" onClick={() => onJump("settings")}><Icon name="settings" size={16} /></button>

      <div style={{ position: "relative" }}>
        <button
          title="实例"
          onClick={() => setMenuOpen(v => !v)}
          style={{
            border: 0, cursor: "pointer", position: "relative",
            width: 28, height: 28, borderRadius: "50%", background: "var(--ink)",
            color: "#fff", display: "inline-flex", alignItems: "center", justifyContent: "center",
            fontFamily: "var(--font-mono)", fontSize: 11.5, fontWeight: 600, letterSpacing: "-0.02em",
          }}>
          JX
          {/* health indicator — small dot top-right; mirrors topbar 通道 state */}
          <span style={{
            position: "absolute", top: -1, right: -1,
            width: 8, height: 8, borderRadius: "50%",
            background: allOk ? "var(--success-fg)" : "var(--warning)",
            boxShadow: "0 0 0 1.5px var(--canvas)",
          }} />
        </button>
        {menuOpen && (
          <div className="card" style={{
            position: "absolute", right: 0, top: 36, width: 260, zIndex: 30,
            boxShadow: "0 12px 36px rgba(0,0,0,0.14)", overflow: "hidden",
          }}>
            <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <KV k="模型" v={model} />
              <KV k="Base" v={(overview && overview.base_url) || "—"} />
              <KV k="运行" v={overview ? `${Math.floor(overview.uptime_sec / 60)} 分钟` : "—"} />
            </div>
            <div style={{ borderTop: "1px solid var(--hairline)", padding: 8, display: "flex", gap: 8 }}>
              <button className="btn btn-secondary btn-sm grow" onClick={() => { setMenuOpen(false); onJump("overview"); }}>总览</button>
              {onLogout && <button className="btn btn-danger-ghost btn-sm grow" onClick={onLogout}>退出</button>}
            </div>
          </div>
        )}
      </div>
    </div>);

}

const TAB_LABEL = {
  overview: "总览",
  chat: "会话",
  persona: "人设",
  memory: "记忆",
  skills: "技能",
  tools: "工具",
  channels: "通道",
  permissions: "权限",
  settings: "设置"
};

const RAIL_SECTIONS = [
{
  label: "概览",
  items: [
  { id: "overview", label: "总览", icon: "metric", count: null },
  { id: "chat", label: "会话", icon: "chat", count: "42" }]

},
{
  label: "身份与上下文",
  items: [
  { id: "persona", label: "人设", icon: "user", count: "5" },
  { id: "memory", label: "记忆", icon: "doc", count: "6" },
  { id: "skills", label: "技能", icon: "spark", count: "3" }]

},
{
  label: "运行时",
  items: [
  { id: "tools", label: "工具", icon: "terminal", count: null },
  { id: "channels", label: "通道", icon: "stack", count: "2/3" },
  { id: "permissions", label: "权限", icon: "lock", count: null }]

},
{
  label: "系统",
  items: [
  { id: "settings", label: "设置", icon: "settings", count: null }]

}];


function LeftRail({ active, onJump, collapsed }) {
  return (
    <aside className="rail" style={{ display: "flex", flexDirection: "column" }}>
      <div style={{ flex: "0 0 auto" }}>
        {RAIL_SECTIONS.map((section, i) =>
        <div key={section.label} style={{ marginBottom: 4 }}>
            <div className="rail-section">
              <div className="rail-section-label">{section.label}</div>
            </div>
            {section.items.map((it) =>
          <div
            key={it.id}
            className={`rail-item ${active === it.id ? "active" : ""}`}
            title={collapsed ? it.label : undefined}
            onClick={() => onJump(it.id)}>

                <span className="rail-glyph"><Icon name={it.icon} size={15} /></span>
                <span>{it.label}</span>
                {it.count && <span className="rail-count">{it.count}</span>}
              </div>
          )}
            {i < RAIL_SECTIONS.length - 1 && <hr className="hr" style={{ margin: "10px 14px" }} />}
          </div>
        )}
      </div>

      {/* Bottom dock — 展开态：详细环境徽章；折叠态：仅一个绿点 */}
      <div className="rail-dock-full" style={{
        marginTop: "auto",
        padding: "12px 14px",
        borderTop: "1px solid var(--hairline)",
        background: "var(--pearl)",
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span className="dot dot-up" />
          <span className="t-row-strong" style={{ color: "var(--ink)", fontSize: 12 }}>本机环境</span>
          <span className="t-meta" style={{ marginLeft: "auto", color: "var(--ink-48)" }}>Py 3.13</span>
        </div>
        <div
          className="t-mono-sm"
          title="~/.sanshiliu"
          style={{
            color: "var(--ink-80)",
            background: "var(--canvas)",
            border: "1px solid var(--hairline)",
            borderRadius: 6,
            padding: "3px 8px",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >~/.sanshiliu</div>
        <div style={{ display: "flex", gap: 4, alignItems: "center", flexWrap: "wrap" }}>
          <span className="chip chip-success chip-dot" style={{ fontSize: 10 }}>REPL</span>
          <span className="chip chip-success chip-dot" style={{ fontSize: 10 }}>Web</span>
          <span className="chip" style={{ fontSize: 10, color: "var(--ink-48)", background: "rgba(0,0,0,0.04)" }}>
            <span className="dot dot-off" />微信
          </span>
        </div>
      </div>
      <div className="rail-dock-mini" title="本机环境 · Py 3.13">
        <span className="dot dot-up" style={{ width: 8, height: 8 }} />
      </div>
    </aside>);

}

/* Page header — sticky strip inside main scroll. Title + optional sub + actions slot. */
function PageHeader({ title, sub, actions, eyebrow }) {
  return (
    <div className="pageheader">
      <div>
        {eyebrow && <div className="t-eyebrow" style={{ marginBottom: 4 }}>{eyebrow}</div>}
        <h1 className="pageheader-title">{title}</h1>
        {sub && <div className="pageheader-sub" style={{ marginTop: 3 }}>{sub}</div>}
      </div>
      <div className="grow" />
      {actions && <div style={{ display: "flex", gap: 8, alignItems: "center" }}>{actions}</div>}
    </div>);

}

Object.assign(window, { TopBar, LeftRail, PageHeader, TAB_LABEL });
