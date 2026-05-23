/* App shell — composes topbar + rail + main content. */

function App() {
  const [view, setView] = React.useState("overview");

  React.useEffect(() => {
    const fromHash = () => {
      const h = window.location.hash.replace("#", "");
      if (["overview","chat","persona","memory","skills","channels","permissions"].includes(h)) setView(h);
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

  let body = null;
  switch (view) {
    case "overview":    body = <Overview     onJump={jump} />; break;
    case "chat":        body = <Chat         onJump={jump} />; break;
    case "persona":     body = <Persona      onJump={jump} />; break;
    case "memory":      body = <Memory       onJump={jump} />; break;
    case "skills":      body = <Skills       onJump={jump} />; break;
    case "channels":    body = <Channels     onJump={jump} />; break;
    case "permissions": body = <Permissions  onJump={jump} />; break;
    default:            body = <Overview     onJump={jump} />;
  }

  return (
    <div className="shell">
      <TopBar active={view} onJump={jump} />
      <LeftRail active={view} onJump={jump} />
      <div className="main" id="main">{body}</div>
    </div>
  );
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);
