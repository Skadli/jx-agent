/* Drawer — 右侧滑入抽屉；portal 渲染，左边缘可拖拽伸缩，宽度记忆 localStorage，
   ESC 或点击 scrim 关闭。Apple 设计语言：白色 + hairline + saturate blur scrim。

   props:
     open       boolean  是否打开
     onClose    fn       关闭回调（ESC / scrim / 关闭按钮触发）
     title      node     顶部标题
     subtitle   node     标题下副标题（可选）
     actions    node     标题右侧的操作按钮（可选）
     storageKey string   宽度持久化键名；默认 "drawer-width-default"
     children   node     抽屉内容（外层带 padding：0；内部自己 padding）
*/

function Drawer({ open, onClose, title, subtitle, actions, storageKey, children }) {
  const skey = storageKey || "drawer-width-default";
  const minW = 480;
  const maxW = Math.min(1400, typeof window !== "undefined" ? window.innerWidth - 240 : 1200);
  const defaultW = Math.min(820, typeof window !== "undefined" ? Math.floor(window.innerWidth * 0.6) : 820);

  const [width, setWidth] = React.useState(() => {
    if (typeof window === "undefined") return defaultW;
    const v = parseInt(window.localStorage.getItem(skey), 10);
    return (Number.isFinite(v) && v >= minW && v <= maxW) ? v : defaultW;
  });
  const draggingRef = React.useRef(false);

  // ESC 关闭
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === "Escape") onClose && onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // 持久化宽度
  React.useEffect(() => {
    if (typeof window === "undefined") return;
    try { window.localStorage.setItem(skey, String(width)); } catch (e) { /* quota 满了忽略 */ }
  }, [width, skey]);

  // 拖拽伸缩
  const onResizeStart = React.useCallback((e) => {
    e.preventDefault();
    draggingRef.current = true;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    const onMove = (mv) => {
      if (!draggingRef.current) return;
      const next = Math.max(minW, Math.min(maxW, window.innerWidth - mv.clientX));
      setWidth(next);
    };
    const onUp = () => {
      draggingRef.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }, [minW, maxW]);

  if (!open) return null;
  if (typeof document === "undefined") return null;

  const node = (
    <React.Fragment>
      {/* scrim：半透明遮罩，点关闭 */}
      <div
        onClick={onClose}
        style={{
          position: "fixed", inset: 0,
          background: "rgba(0,0,0,0.32)",
          zIndex: 40,
          animation: "drawer-fade-in 180ms cubic-bezier(0.32,0.72,0,1)",
        }}
      />
      {/* 抽屉本体 */}
      <aside
        style={{
          position: "fixed", top: 0, right: 0, bottom: 0,
          width: width + "px",
          background: "var(--canvas)",
          borderLeft: "1px solid var(--hairline-strong)",
          boxShadow: "-12px 0 32px -16px rgba(0,0,0,0.18)",
          zIndex: 41,
          display: "flex", flexDirection: "column",
          animation: "drawer-slide-in 240ms cubic-bezier(0.32,0.72,0,1)",
        }}
      >
        {/* 拖拽手柄：宽 6px 的左边缘热区 */}
        <div
          onMouseDown={onResizeStart}
          title="拖拽调整宽度"
          style={{
            position: "absolute", top: 0, bottom: 0, left: -3,
            width: 6, cursor: "col-resize",
            zIndex: 42,
          }}
        />
        {/* 顶部 header */}
        <div style={{
          flexShrink: 0,
          padding: "14px 20px",
          borderBottom: "1px solid var(--hairline)",
          display: "flex", alignItems: "center", gap: 12,
          minWidth: 0,
        }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="t-card-title" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{title}</div>
            {subtitle && <div className="t-meta" style={{ marginTop: 3 }}>{subtitle}</div>}
          </div>
          {actions}
          <button className="btn-icon" onClick={onClose} title="关闭 (ESC)">
            <Icon name="x" size={16} />
          </button>
        </div>
        {/* 内容区 */}
        <div style={{ flex: 1, overflow: "auto", minHeight: 0 }}>
          {children}
        </div>
      </aside>
      <style>{`
        @keyframes drawer-slide-in {
          from { transform: translateX(20px); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
        @keyframes drawer-fade-in {
          from { opacity: 0; }
          to { opacity: 1; }
        }
      `}</style>
    </React.Fragment>
  );

  return ReactDOM.createPortal(node, document.body);
}

Object.assign(window, { Drawer });
