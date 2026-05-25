/* Shared primitives — admin density. Icons + small composable display elements. */

function Icon({ name, size = 16, color = "currentColor", strokeWidth = 1.5 }) {
  const s = size,sw = strokeWidth,c = color;
  const common = { width: s, height: s, viewBox: "0 0 24 24", fill: "none", stroke: c, strokeWidth: sw, strokeLinecap: "round", strokeLinejoin: "round" };
  switch (name) {
    case "brand":return (
      <svg viewBox="0 0 24 24" width={s} height={s} fill="none">
        {/* Rounded chassis — echoes the boxed ASCII banner */}
        <rect x="2.5" y="3.5" width="19" height="17" rx="3.5" stroke={c} strokeWidth="1.6"/>
        {/* Two content lines */}
        <line x1="6"   y1="9"    x2="18" y2="9"    stroke={c} strokeWidth="1.6" strokeLinecap="round"/>
        <line x1="6"   y1="12.5" x2="13" y2="12.5" stroke={c} strokeWidth="1.6" strokeLinecap="round"/>
        {/* Action-blue accent line — matches the underscore under 笑 in the wordmark */}
        <line x1="6"   y1="16"   x2="14" y2="16"   stroke="var(--primary)" strokeWidth="1.8" strokeLinecap="round"/>
      </svg>
    );
    case "search":return <svg {...common}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>;
    case "user":return <svg {...common}><circle cx="12" cy="8" r="4" /><path d="M4 20a8 8 0 0 1 16 0" /></svg>;
    case "send":return <svg {...common}><path d="M22 2 11 13" /><path d="M22 2l-7 20-4-9-9-4 20-7z" /></svg>;
    case "plus":return <svg {...common}><path d="M12 5v14M5 12h14" /></svg>;
    case "minus":return <svg {...common}><path d="M5 12h14" /></svg>;
    case "check":return <svg {...common}><path d="M5 12.5 9.5 17 19 7" /></svg>;
    case "x":return <svg {...common}><path d="M6 6l12 12M18 6 6 18" /></svg>;
    case "chevron-r":return <svg {...common}><path d="m9 6 6 6-6 6" /></svg>;
    case "chevron-d":return <svg {...common}><path d="m6 9 6 6 6-6" /></svg>;
    case "chevron-u":return <svg {...common}><path d="m18 15-6-6-6 6" /></svg>;
    case "external":return <svg {...common}><path d="M14 4h6v6" /><path d="M20 4 10 14" /><path d="M20 14v6H4V4h6" /></svg>;
    case "settings":return <svg {...common}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01A1.65 1.65 0 0 0 10 4.09V4a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V10a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></svg>;
    case "terminal":return <svg {...common}><rect x="2" y="4" width="20" height="16" rx="2" /><path d="m6 9 3 3-3 3M12 15h5" /></svg>;
    case "globe":return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" /></svg>;
    case "wechat":return <svg {...common}><path d="M9 4a7 7 0 0 0-7 7c0 2 .9 3.8 2.4 5L4 19l2.4-1.3A7 7 0 0 0 9 18" /><circle cx="6.5" cy="9.5" r=".6" fill={c} stroke="none" /><circle cx="10.5" cy="9.5" r=".6" fill={c} stroke="none" /><path d="M21 15c0-2.8-2.7-5-6-5s-6 2.2-6 5 2.7 5 6 5l1.5-.2L19 21l-.5-1.6A4.8 4.8 0 0 0 21 15z" /></svg>;
    case "qr":return <svg {...common}><rect x="3" y="3" width="7" height="7" /><rect x="14" y="3" width="7" height="7" /><rect x="3" y="14" width="7" height="7" /><path d="M14 14h3v3M20 14v3M14 17v4M17 20h4" /></svg>;
    case "lock":return <svg {...common}><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></svg>;
    case "stack":return <svg {...common}><path d="m12 3 9 5-9 5-9-5 9-5z" /><path d="m3 13 9 5 9-5M3 18l9 5 9-5" /></svg>;
    case "spark":return <svg {...common}><path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.8 2.8M15.2 15.2 18 18M6 18l2.8-2.8M15.2 8.8 18 6" /></svg>;
    case "doc":return <svg {...common}><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" /><path d="M14 3v6h6M8 13h8M8 17h5" /></svg>;
    case "folder":return <svg {...common}><path d="M3 6a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></svg>;
    case "trash":return <svg {...common}><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" /></svg>;
    case "edit":return <svg {...common}><path d="M14 4l6 6L8 22H2v-6L14 4z" /></svg>;
    case "history":return <svg {...common}><path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5M12 7v5l3 2" /></svg>;
    case "metric":return <svg {...common}><path d="M3 3v18h18" /><path d="m7 14 3-3 4 4 5-7" /></svg>;
    case "chat":return <svg {...common}><path d="M21 12a8 8 0 0 1-11.3 7.3L4 21l1.7-5.7A8 8 0 1 1 21 12z" /></svg>;
    case "filter":return <svg {...common}><path d="M4 5h16M7 12h10M10 19h4" /></svg>;
    case "refresh":return <svg {...common}><path d="M3 12a9 9 0 0 1 15.5-6.3L21 8" /><path d="M21 3v5h-5" /><path d="M21 12a9 9 0 0 1-15.5 6.3L3 16" /><path d="M3 21v-5h5" /></svg>;
    case "more":return <svg {...common}><circle cx="12" cy="6" r="1.4" /><circle cx="12" cy="12" r="1.4" /><circle cx="12" cy="18" r="1.4" /></svg>;
    case "arrow-r":return <svg {...common}><path d="M5 12h14M13 6l6 6-6 6" /></svg>;
    case "play":return <svg {...common}><polygon points="6 4 20 12 6 20 6 4" fill={c} stroke="none" /></svg>;
    case "pause":return <svg {...common}><rect x="6" y="4" width="4" height="16" /><rect x="14" y="4" width="4" height="16" /></svg>;
    case "stop":return <svg {...common}><rect x="5" y="5" width="14" height="14" rx="2" fill={c} stroke="none" /></svg>;
    case "alert":return <svg {...common}><path d="M12 9v4M12 17.5v.01" /><path d="M10.3 3.9 2.4 17a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /></svg>;
    case "info":return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7.5v.01" /></svg>;
    case "copy":return <svg {...common}><rect x="8" y="8" width="13" height="13" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></svg>;
    case "download":return <svg {...common}><path d="M12 4v12M6 12l6 6 6-6M4 20h16" /></svg>;
    case "menu":return <svg {...common}><path d="M4 6h16M4 12h16M4 18h16" /></svg>;
    case "menu-collapse":return <svg {...common}><path d="M4 6h16M4 12h10M4 18h16" /></svg>;
    default:return null;
  }
}

/* StatCard — KPI on a card, label above, value big. */
function StatCard({ label, value, unit, sub, trend, color = "var(--ink)" }) {
  return (
    <div className="card card-padded">
      <div className="t-eyebrow">{label}</div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginTop: 8 }}>
        <span className="t-stat" style={{ color }}>{value}</span>
        {unit && <span className="t-body" style={{ color: "var(--ink-60)" }}>{unit}</span>}
        {trend &&
        <span className={`chip ${trend.kind === "up" ? "chip-success" : trend.kind === "down" ? "chip-danger" : ""}`} style={{ marginLeft: "auto", fontSize: 11 }}>
            {trend.kind === "up" ? "↑" : trend.kind === "down" ? "↓" : "→"} {trend.value}
          </span>
        }
      </div>
      {sub && <div className="t-meta" style={{ marginTop: 6 }}>{sub}</div>}
    </div>);

}

/* KV — label / value row, mono value. Used in inspector panels. */
function KV({ k, v, mono, accent }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "baseline" }}>
      <span className="t-meta" style={{ color: "var(--ink-60)" }}>{k}</span>
      <span
        style={{
          fontFamily: mono === false ? "var(--font-text)" : "var(--font-mono)",
          fontSize: 12.5,
          color: accent || "var(--ink)",
          textAlign: "right"
        }}>
        {v}</span>
    </div>);

}

/* Horizontal meter. Single-color, no gradient. */
function Meter({ value, max = 100, color = "var(--primary)", height = 4, bg = "var(--hairline)" }) {
  const pct = Math.max(0, Math.min(100, value / max * 100));
  return (
    <div style={{ height, background: bg, borderRadius: 999, overflow: "hidden" }}>
      <div style={{ width: `${pct}%`, height: "100%", background: color, transition: "width 320ms cubic-bezier(0.32,0.72,0,1)" }} />
    </div>);

}

/* CardHeader — title + optional right slot. Used inside .card. */
function CardHeader({ title, sub, right, eyebrow }) {
  return (
    <div className="card-header">
      <div>
        {eyebrow && <div className="t-eyebrow" style={{ marginBottom: 3 }}>{eyebrow}</div>}
        <div className="t-card-title">{title}</div>
        {sub && <div className="t-meta" style={{ marginTop: 2 }}>{sub}</div>}
      </div>
      {right && <div style={{ display: "flex", alignItems: "center", gap: 8 }}>{right}</div>}
    </div>);

}

/* Status row — dot + label + value. */
function StatusRow({ status, label, value }) {
  const dotClass =
  status === "up" ? "dot-up" :
  status === "warn" ? "dot-warn" :
  status === "down" ? "dot-down" : "dot-off";
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "6px 0" }}>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 9 }}>
        <span className={`dot ${dotClass}`} />
        <span className="t-row" style={{ color: "var(--ink)" }}>{label}</span>
      </span>
      <span className="t-mono-sm" style={{ color: "var(--ink-60)" }}>{value}</span>
    </div>);

}

/* SegmentedToggle */
function Segmented({ value, options, onChange }) {
  return (
    <div className="seg">
      {options.map((o) =>
      <button
        key={o.id}
        className={`seg-item ${value === o.id ? "active" : ""}`}
        onClick={() => onChange(o.id)}>
        {o.label}</button>
      )}
    </div>);

}

/* Toggle */
function Toggle({ on, onChange }) {
  return (
    <span className={`toggle ${on ? "on" : ""}`} onClick={() => onChange(!on)} role="switch" aria-checked={on} />);

}

/**
 * ResponsiveTable — 容器自适应表格。
 * 用 ResizeObserver 监听自身宽度；宽度 < cardMinWidth 时切换到 KV 卡片栈，
 * 否则用标准 .tbl 表格渲染。columns 描述列；rows 是数据。
 *
 * columns: [{ key, label, width?, align?, mono?, render?(row) => node, hideInCards?: bool }]
 * rowKey:  (row) => string
 * onRowClick / isRowActive 可选
 * emptyText: 空数据展示
 * cardMinWidth: 切到卡片视图的阈值（默认 520px）
 */
function ResponsiveTable({
  columns,
  rows,
  rowKey,
  onRowClick,
  isRowActive,
  emptyText = "暂无数据",
  cardMinWidth = 520,
}) {
  const wrapRef = React.useRef(null);
  const [width, setWidth] = React.useState(0);

  React.useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(entries => {
      for (const e of entries) setWidth(Math.floor(e.contentRect.width));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  if (!rows || rows.length === 0) {
    return (
      <div ref={wrapRef}>
        <div className="t-meta" style={{ padding: 32, textAlign: "center", color: "var(--ink-60)" }}>{emptyText}</div>
      </div>
    );
  }

  const useCards = width > 0 && width < cardMinWidth;

  if (useCards) {
    return (
      <div ref={wrapRef} style={{ display: "flex", flexDirection: "column" }}>
        {rows.map((row, idx) => {
          const active = isRowActive ? isRowActive(row) : false;
          return (
            <div
              key={rowKey(row)}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              style={{
                padding: "12px 14px",
                borderTop: idx === 0 ? "none" : "1px solid var(--divider-soft)",
                background: active ? "var(--primary-soft)" : "transparent",
                cursor: onRowClick ? "pointer" : "default",
                display: "flex", flexDirection: "column", gap: 4,
              }}>
              {columns.filter(c => !c.hideInCards).map(col => {
                const value = col.render ? col.render(row) : row[col.key];
                if (value == null || value === "" || value === false) return null;
                return (
                  <div key={col.key} style={{
                    display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start",
                  }}>
                    <span className="t-meta" style={{ color: "var(--ink-60)", flex: "0 0 auto" }}>{col.label}</span>
                    <span style={{
                      textAlign: "right", minWidth: 0,
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                      fontFamily: col.mono ? "var(--font-mono)" : "var(--font-text)",
                      fontSize: 12.5, color: "var(--ink)",
                    }}>{value}</span>
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
    );
  }

  return (
    <div ref={wrapRef} style={{ overflow: "hidden" }}>
      {/* tableLayout: fixed —— 未设 width 的列共享剩余空间，超长内容由 td overflow:hidden 截断；
          auto 模式下超长 JSON 会撑开列宽 → 整表 > 容器 → 横向滚动条。 */}
      <table className="tbl" style={{ tableLayout: "fixed" }}>
        <thead>
          <tr>
            {columns.map(col => (
              <th key={col.key} style={{
                width: col.width,
                textAlign: col.align || "left",
                whiteSpace: "nowrap",
              }}>{col.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(row => {
            const active = isRowActive ? isRowActive(row) : false;
            return (
              <tr
                key={rowKey(row)}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                style={{
                  cursor: onRowClick ? "pointer" : "default",
                  background: active ? "var(--primary-soft)" : "transparent",
                }}>
                {columns.map(col => (
                  <td key={col.key} style={{
                    textAlign: col.align || "left",
                    fontFamily: col.mono ? "var(--font-mono)" : undefined,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}>
                    {col.render ? col.render(row) : row[col.key]}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Pagination — 通用分页控件。
 * 显示「上一页 / 第 X / N 页 / 下一页」+ 左侧可选 info 文案。
 * 单页时整体隐藏。
 */
function Pagination({ page, totalPages, onChange, info }) {
  if (totalPages <= 1) return info ? (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between",
      padding: "10px 16px", borderTop: "1px solid var(--hairline)", gap: 12,
    }}>
      <span className="t-meta" style={{ color: "var(--ink-60)" }}>{info}</span>
    </div>
  ) : null;

  const go = p => onChange(Math.max(1, Math.min(totalPages, p)));
  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between",
      padding: "10px 16px", borderTop: "1px solid var(--hairline)",
      gap: 12, flexWrap: "wrap",
    }}>
      <span className="t-meta" style={{ color: "var(--ink-60)" }}>{info}</span>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <button
          className="btn btn-secondary btn-sm"
          disabled={page <= 1}
          onClick={() => go(page - 1)}
          title="上一页">
          <Icon name="chevron-r" size={11} /> 上一页
        </button>
        <span className="t-meta" style={{
          color: "var(--ink)", padding: "0 8px",
          minWidth: 80, textAlign: "center", fontFamily: "var(--font-mono)",
        }}>
          {page} / {totalPages}
        </span>
        <button
          className="btn btn-secondary btn-sm"
          disabled={page >= totalPages}
          onClick={() => go(page + 1)}
          title="下一页">
          下一页 <Icon name="chevron-r" size={11} />
        </button>
      </div>
    </div>
  );
}

Object.assign(window, { Icon, StatCard, KV, Meter, CardHeader, StatusRow, Segmented, Toggle, ResponsiveTable, Pagination });