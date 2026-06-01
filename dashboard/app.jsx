/* App shell — composes topbar + rail + main content. */

const RAIL_STATE_KEY = "jx_rail_collapsed";
const MOBILE_BP = 640;

function App() {
  const [view, setView] = React.useState("overview");
  const [auth, setAuth] = React.useState({ checking: true, authed: false, configured: false });
  // 侧栏折叠状态；桌面默认展开，手机默认折叠（用 matchMedia 判一次）
  const [railCollapsed, setRailCollapsed] = React.useState(() => {
    try {
      const saved = localStorage.getItem(RAIL_STATE_KEY);
      if (saved === "1") return true;
      if (saved === "0") return false;
    } catch (e) {}
    return typeof window !== "undefined" && window.innerWidth < 1024;
  });
  // 手机端抽屉是否打开（折叠/展开是桌面态，open 是 ≤640 时覆盖出现）
  const [railOpen, setRailOpen] = React.useState(false);
  const [isMobile, setIsMobile] = React.useState(
    typeof window !== "undefined" && window.innerWidth <= MOBILE_BP
  );

  React.useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth <= MOBILE_BP);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const toggleRail = React.useCallback(() => {
    if (isMobile) {
      setRailOpen(o => !o);
      return;
    }
    setRailCollapsed(c => {
      const next = !c;
      try { localStorage.setItem(RAIL_STATE_KEY, next ? "1" : "0"); } catch (e) {}
      return next;
    });
  }, [isMobile]);

  const refreshAuth = React.useCallback(async () => {
    const r = await API.authStatus();
    if (r.error) {
      setAuth({ checking: false, authed: false, configured: true });
      return;
    }
    setAuth({
      checking: false,
      authed: !r.configured || !!r.authenticated,
      configured: !!r.configured,
    });
  }, []);

  React.useEffect(() => {
    refreshAuth();
  }, [refreshAuth]);

  React.useEffect(() => {
    const fromHash = () => {
      const h = window.location.hash.replace("#", "");
      if (["overview","chat","persona","memory","skills","tools","channels","permissions","heartbeat","growth","settings"].includes(h)) setView(h);
    };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    return () => window.removeEventListener("hashchange", fromHash);
  }, []);

  const jump = (v) => {
    setView(v);
    window.location.hash = v;
    // 移动端点导航后自动收起抽屉
    if (isMobile) setRailOpen(false);
    // scroll main back to top
    const main = document.getElementById("main");
    if (main) main.scrollTop = 0;
  };

  const logout = async () => {
    await API.logout();
    setAuth({ checking: false, authed: false, configured: true });
  };

  if (auth.checking) return <LoginShell muted text="正在检查面板权限…" />;
  if (!auth.authed) return <LoginGate onLogin={refreshAuth} />;

  let body = null;
  switch (view) {
    case "overview":    body = <Overview     onJump={jump} />; break;
    case "chat":        body = <Chat         onJump={jump} />; break;
    case "persona":     body = <Persona      onJump={jump} />; break;
    case "memory":      body = <Memory       onJump={jump} />; break;
    case "skills":      body = <Skills       onJump={jump} />; break;
    case "tools":       body = <Tools        onJump={jump} />; break;
    case "channels":    body = <Channels     onJump={jump} />; break;
    case "permissions": body = <Permissions  onJump={jump} />; break;
    case "heartbeat":   body = <Heartbeat    onJump={jump} />; break;
    case "growth":      body = <Growth       onJump={jump} />; break;
    case "settings":    body = <Settings     onJump={jump} />; break;
    default:            body = <Overview     onJump={jump} />;
  }

  const shellClass = [
    "shell",
    !isMobile && railCollapsed ? "rail-collapsed" : "",
    isMobile && railOpen ? "rail-open" : "",
  ].filter(Boolean).join(" ");

  return (
    <div className={shellClass}>
      <TopBar
        active={view}
        onJump={jump}
        onLogout={auth.configured ? logout : null}
        onToggleRail={toggleRail}
        railCollapsed={!isMobile && railCollapsed} />
      <LeftRail active={view} onJump={jump} collapsed={!isMobile && railCollapsed} />
      {isMobile && railOpen && <div className="rail-scrim" onClick={() => setRailOpen(false)} />}
      <div className="main" id="main">{body}</div>
    </div>
  );
}

function LoginShell({ text, muted }) {
  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", background: "var(--parchment)" }}>
      <div className="card card-padded" style={{ width: 360 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <Icon name="brand" size={22} color="var(--ink)" />
          <div>
            <div className="t-card-title">三十六贱笑</div>
            <div className="t-meta" style={{ color: muted ? "var(--ink-60)" : "var(--ink)" }}>{text}</div>
          </div>
        </div>
      </div>
    </div>
  );
}

function LoginGate({ onLogin }) {
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    const r = await API.login(password);
    setBusy(false);
    if (r.error) {
      setError(r.error === "password incorrect" ? "密码不正确" : r.error);
      return;
    }
    onLogin();
  };

  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", background: "var(--parchment)" }}>
      <form className="card card-padded" onSubmit={submit} style={{ width: 380 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 18 }}>
          <Icon name="lock" size={22} color="var(--ink)" />
          <div>
            <div className="t-card-title">输入面板密码</div>
            
          </div>
        </div>
        <input
          className="field"
          type="password"
          autoFocus
          value={password}
          onChange={e => setPassword(e.target.value)}
          placeholder="密码"
        />
        {error && <div className="t-meta" style={{ marginTop: 10, color: "var(--danger)" }}>{error}</div>}
        <button className="btn btn-primary" disabled={busy || !password} style={{ width: "100%", marginTop: 14 }}>
          {busy ? "验证中…" : "进入面板"}
        </button>
      </form>
    </div>
  );
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);
