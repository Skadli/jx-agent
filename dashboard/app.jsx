/* App shell — composes topbar + rail + main content. */

function App() {
  const [view, setView] = React.useState("overview");
  const [auth, setAuth] = React.useState({ checking: true, authed: false, configured: false });

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
      if (["overview","chat","persona","memory","skills","tools","channels","permissions"].includes(h)) setView(h);
    };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    return () => window.removeEventListener("hashchange", fromHash);
  }, []);

  const jump = (v) => {
    setView(v);
    window.location.hash = v;
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
    default:            body = <Overview     onJump={jump} />;
  }

  return (
    <div className="shell">
      <TopBar active={view} onJump={jump} onLogout={auth.configured ? logout : null} />
      <LeftRail active={view} onJump={jump} />
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
