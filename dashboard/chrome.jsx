/* Admin chrome — top bar (52px) + left rail (240px).
 * Pure white surfaces, hairline borders, single blue accent on active rail item.
 */

function TopBar({ active, onJump }) {
  return (
    <div className="topbar">
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

      <div className="search-wrap" style={{ width: 280 }}>
        <span className="search-icon"><Icon name="search" size={14} color="var(--ink-48)" /></span>
        <input className="search" placeholder="搜索会话 / 文件 / 命令  ⌘K" />
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
        <span className="dot dot-up" />
        <span className="t-meta" style={{ color: "var(--ink-80)" }}>所有通道正常</span>
      </div>

      <div style={{ height: 14, width: 1, background: "var(--hairline)" }} />

      <span className="chip chip-mono" title="当前模型">
        gpt-4o-mini
      </span>

      <button className="btn-icon" title="设置"><Icon name="settings" size={16} /></button>

      <div style={{
        width: 28, height: 28, borderRadius: "50%", background: "var(--ink)",
        color: "#fff", display: "inline-flex", alignItems: "center", justifyContent: "center",
        fontSize: 11, fontWeight: 600, letterSpacing: 0
      }}>JX</div>
    </div>);

}

const TAB_LABEL = {
  overview: "总览",
  chat: "会话",
  persona: "人设",
  memory: "记忆",
  skills: "技能",
  channels: "通道",
  permissions: "权限"
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
  { id: "channels", label: "通道", icon: "stack", count: "2/3" },
  { id: "permissions", label: "权限", icon: "lock", count: null }]

}];


function LeftRail({ active, onJump }) {
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

      {/* Bottom dock — environment badge, pinned to bottom */}
      <div style={{
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