/* Chat / Sessions — operator view.
 * Three columns: session list (left) + conversation (center) + inspector (right).
 * Composer is embedded in the center pane, not a floating sticky bar.
 */

const SEED_CHAT = [
  { role: "user",  text: "我想拍一个'我和女朋友吵架'的视频，但怕翻车", t: "14:02:14", tokens: 38 },
  { role: "agent", text: "先停一下。你说的'我和女朋友吵架'是想真拍现场，还是用一个荒诞设定包装？这俩天差地远。", t: "14:02:16", tokens: 42, latency_ms: 1140 },
  { role: "user",  text: "我想真拍", t: "14:03:02", tokens: 12 },
  { role: "agent", text: "那就别拍。真吵架拍一次毁一次关系。换个思路——做成「测评一下我和女朋友的吵架风格」，找她事先同意，把规则定好：限定话题、限定时长 3 分钟、最后两人复盘谁吵得更离谱。这样真实情绪在、节目机制也在、还有结尾把话筒扔给观众。", t: "14:03:05", tokens: 178, latency_ms: 1380, tool: "web_search", tool_ms: 412 },
];
const STREAM_REPLY = "避开'吵架'两个字，太直接，给一个离谱任务入口。可以是「测评一下情侣吵架谁的话术更逆天」，或者「我和我女友办了一场吵架奥运会」。第二个更野，建议你先做一期看看反馈。";

const ALL_SESSIONS = [
  { id: "repl-8f2a",  ch: "REPL",  status: "active",  tokens: 6142,  cost: "0.0073", t: "现在",       last: "标题怎么起",                          msgs: 4 },
  { id: "web-2c91",   ch: "Web",   status: "idle",    tokens: 18902, cost: "0.0187", t: "14 分钟前",  last: "情侣吵架的视频但怕翻车",              msgs: 12 },
  { id: "wechat-a3",  ch: "微信",  status: "closed",  tokens: 612,   cost: "0.0006", t: "1 小时前",   last: "在吗",                                msgs: 2 },
  { id: "repl-71d0",  ch: "REPL",  status: "closed",  tokens: 11408, cost: "0.0114", t: "3 小时前",   last: "把「我做了 X」改成离谱任务",          msgs: 18 },
  { id: "web-44ce",   ch: "Web",   status: "closed",  tokens: 2841,  cost: "0.0028", t: "昨天",       last: "热梗总结怎么排梗",                    msgs: 6 },
  { id: "web-1aa9",   ch: "Web",   status: "closed",  tokens: 5612,  cost: "0.0056", t: "昨天",       last: "情景剧脚本改一下",                    msgs: 9 },
  { id: "repl-3f12",  ch: "REPL",  status: "closed",  tokens: 4101,  cost: "0.0041", t: "2 天前",     last: "/persona 看一下",                     msgs: 3 },
];

function Chat({ onJump }) {
  const [activeId, setActiveId] = React.useState("repl-8f2a");
  const [filter, setFilter] = React.useState("all");
  const [messages, setMessages] = React.useState(SEED_CHAT);
  const [composer, setComposer] = React.useState("标题怎么起");
  const [streaming, setStreaming] = React.useState(false);
  const [streamText, setStreamText] = React.useState("");

  const sessions = ALL_SESSIONS.filter(s =>
    filter === "all" ||
    (filter === "active" && s.status === "active") ||
    (filter === "repl"   && s.ch === "REPL") ||
    (filter === "web"    && s.ch === "Web")  ||
    (filter === "wechat" && s.ch === "微信")
  );

  const active = ALL_SESSIONS.find(s => s.id === activeId) || ALL_SESSIONS[0];

  const send = () => {
    if (!composer.trim() || streaming) return;
    setMessages(m => [...m, { role: "user", text: composer.trim(), t: "现在", tokens: composer.length }]);
    setComposer("");
    setStreaming(true);
    setStreamText("");
    let i = 0;
    const tick = () => {
      if (i >= STREAM_REPLY.length) {
        setMessages(m => [...m, { role: "agent", text: STREAM_REPLY, t: "现在", tokens: 96, latency_ms: 1080 }]);
        setStreamText("");
        setStreaming(false);
        return;
      }
      i = Math.min(STREAM_REPLY.length, i + (2 + Math.floor(Math.random() * 4)));
      setStreamText(STREAM_REPLY.slice(0, i));
      setTimeout(tick, 28);
    };
    setTimeout(tick, 280);
  };

  return (
    <div data-screen-label="02 会话" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <PageHeader
        title="会话"
        sub="42 条 · 1 活跃 · 累计 218,402 tokens · ￥0.4271"
        actions={
          <>
            <Segmented value={filter} onChange={setFilter} options={[
              { id: "all",    label: "全部" },
              { id: "active", label: "活跃" },
              { id: "repl",   label: "REPL" },
              { id: "web",    label: "Web" },
              { id: "wechat", label: "微信" },
            ]} />
            <button className="btn btn-secondary"><Icon name="download" size={13}/>导出</button>
            <button className="btn btn-primary"><Icon name="plus" size={13} color="#fff"/>新建会话</button>
          </>
        }
      />

      <div style={{
        display: "grid",
        gridTemplateColumns: "320px 1fr 340px",
        gap: 16,
        padding: "16px 28px 24px",
        flex: 1,
        minHeight: 0,
        alignItems: "stretch",
      }}>
        {/* ===== LEFT: session list ===== */}
        <div className="card" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <div className="card-header" style={{ padding: "10px 14px" }}>
            <div className="search-wrap grow">
              <span className="search-icon"><Icon name="search" size={13} color="var(--ink-48)"/></span>
              <input className="search" placeholder="搜索会话 ID 或文本"/>
            </div>
          </div>
          <div style={{ overflowY: "auto", flex: 1 }}>
            {sessions.map(s => (
              <SessionListItem
                key={s.id}
                s={s}
                active={s.id === activeId}
                onClick={() => setActiveId(s.id)}
              />
            ))}
          </div>
        </div>

        {/* ===== CENTER: conversation ===== */}
        <div className="card" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
          {/* Session header */}
          <div className="card-header">
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <span className={`chip ${active.ch === "微信" ? "" : active.ch === "Web" ? "chip-info" : "chip-success"}`}
                    style={active.ch === "微信" ? { background: "rgba(193,60,123,0.10)", color: "#9c2f5f" } : {}}>
                {active.ch}
              </span>
              <span className="t-mono-strong" style={{ color: "var(--ink)" }}>{active.id}</span>
              <span className={`chip chip-dot ${active.status === "active" ? "chip-success" : ""}`}
                    style={active.status !== "active" ? { color: "var(--ink-60)" } : {}}>
                {active.status === "active" ? "活跃" : active.status === "idle" ? "闲置" : "已关闭"}
              </span>
              <span className="t-meta" style={{ color: "var(--ink-60)" }}>
                {active.msgs} 消息 · {active.tokens.toLocaleString()} tokens · ￥{active.cost} · 开始于 {active.t}
              </span>
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <button className="btn btn-ghost btn-sm">/stats</button>
              <button className="btn btn-ghost btn-sm">/persona</button>
              <button className="btn btn-ghost btn-sm">/memory</button>
              <button className="btn-icon" title="更多"><Icon name="more" size={14}/></button>
            </div>
          </div>

          {/* Transcript */}
          <div style={{ flex: 1, overflowY: "auto", padding: "20px 28px" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
              {messages.map((m, i) => <Bubble key={i} {...m} />)}
              {streaming && <Bubble role="agent" text={streamText} streaming t="正在生成" />}
            </div>
          </div>

          {/* Composer */}
          <div style={{ borderTop: "1px solid var(--hairline)", padding: "12px 16px", background: "var(--pearl)" }}>
            <div style={{ display: "flex", gap: 10, alignItems: "flex-end" }}>
              <textarea
                rows={2}
                className="field field-mono"
                style={{ resize: "none", flex: 1, fontFamily: "var(--font-mono)" }}
                placeholder="说人话。Cmd / Ctrl + Enter 发送。"
                value={composer}
                onChange={e => setComposer(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send(); } }}
              />
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <button className="btn btn-primary" onClick={send} disabled={streaming}>
                  {streaming ? "在算…" : <><Icon name="send" size={13} color="#fff"/>发送</>}
                </button>
                {streaming && <button className="btn btn-secondary btn-sm" onClick={() => setStreaming(false)}><Icon name="stop" size={11}/>停止</button>}
              </div>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8 }}>
              <span className="t-meta">工具 · web_search · file_read · file_write · bash_exec</span>
              <span className="t-meta">模型 · gpt-4o-mini · 上下文 69% · ⌘+Enter 发送</span>
            </div>
          </div>
        </div>

        {/* ===== RIGHT: inspector ===== */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16, overflow: "hidden" }}>
          <div style={{ overflowY: "auto", display: "flex", flexDirection: "column", gap: 16 }}>
            <InspectorBudget />
            <InspectorTools />
            <InspectorSkills onJump={onJump} />
            <InspectorMemory onJump={onJump} />
          </div>
        </div>
      </div>
    </div>
  );
}

function SessionListItem({ s, active, onClick }) {
  const chColor = s.ch === "微信" ? "#9c2f5f" : s.ch === "Web" ? "var(--primary)" : "var(--success-fg)";
  return (
    <div
      onClick={onClick}
      style={{
        padding: "12px 14px",
        borderLeft: active ? "3px solid var(--primary)" : "3px solid transparent",
        borderBottom: "1px solid var(--divider-soft)",
        background: active ? "var(--primary-soft)" : "transparent",
        cursor: "pointer",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {s.status === "active" && <span className="dot dot-up" />}
          <span className="t-mono-strong" style={{ color: active ? "var(--primary)" : "var(--ink)" }}>{s.id}</span>
        </div>
        <span className="t-meta">{s.t}</span>
      </div>
      <div className="t-row" style={{ color: "var(--ink)", marginTop: 6, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.last}</div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8 }}>
        <span className="t-meta" style={{ color: chColor, fontWeight: 500 }}>{s.ch}</span>
        <span className="t-meta">{s.msgs} 消息</span>
        <span className="t-meta">·</span>
        <span className="t-meta">{(s.tokens / 1000).toFixed(1)}k tok</span>
        <span className="t-meta">·</span>
        <span className="t-meta">￥{s.cost}</span>
      </div>
    </div>
  );
}

function Bubble({ role, text, streaming, t, tokens, latency_ms, tool, tool_ms }) {
  const isAgent = role === "agent";
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <span style={{
          width: 22, height: 22, borderRadius: "50%",
          background: isAgent ? "var(--ink)" : "var(--primary-soft-2)",
          color: isAgent ? "#fff" : "var(--primary)",
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          fontSize: 10, fontWeight: 600,
        }}>{isAgent ? "贱" : "你"}</span>
        <span className="t-row-strong" style={{ color: "var(--ink)" }}>{isAgent ? "三十六贱笑" : "操作者"}</span>
        <span className="t-meta" style={{ color: "var(--ink-60)" }}>{t}</span>
        {tool && <span className="chip chip-info"><Icon name="spark" size={11} color="var(--primary)"/>{tool} · {tool_ms}ms</span>}
        {latency_ms && <span className="t-meta" style={{ marginLeft: "auto", color: "var(--ink-60)" }}>{tokens} tok · {latency_ms}ms 首字</span>}
        {!latency_ms && tokens && <span className="t-meta" style={{ marginLeft: "auto", color: "var(--ink-60)" }}>{tokens} tok</span>}
      </div>
      <div style={{
        marginLeft: 30,
        padding: isAgent ? "14px 16px" : 0,
        background: isAgent ? "var(--canvas)" : "transparent",
        border: isAgent ? "1px solid var(--hairline)" : "none",
        borderRadius: 10,
      }}>
        <div style={{
          fontFamily: "var(--font-text)",
          fontSize: 14,
          lineHeight: 1.55,
          letterSpacing: "-0.012em",
          color: "var(--ink)",
          whiteSpace: "pre-wrap",
        }}>
          {text}
          {streaming && <span className="blink-cursor" style={{ color: "var(--primary)", marginLeft: 2 }}>▮</span>}
        </div>
      </div>
    </div>
  );
}

function InspectorBudget() {
  return (
    <div className="card">
      <CardHeader title="上下文" />
      <div className="card-body">
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
          <span className="t-stat-sm">69%</span>
          <span className="t-mono-sm" style={{ color: "var(--ink-60)" }}>88,412 / 128k</span>
        </div>
        <div style={{ marginTop: 10 }}>
          <Meter value={88412} max={128000} color="var(--primary)" height={5} />
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 14 }}>
          <KV k="compact 阈值" v="80%" />
          <KV k="compact / micro" v="2 / 7" />
          <KV k="cache 命中" v="14" />
          <KV k="平均 TPS" v="62.4" />
        </div>
      </div>
    </div>
  );
}

function InspectorTools() {
  const tools = [
    { name: "web_search",  on: true, calls: 3 },
    { name: "file_read",   on: true, calls: 2 },
    { name: "file_write",  on: true, calls: 0 },
    { name: "bash_exec",   on: true, calls: 1 },
    { name: "code_interp", on: true, calls: 0 },
    { name: "http_post",   on: false, calls: 0 },
  ];
  return (
    <div className="card">
      <CardHeader title="工具" sub="本次会话可用工具" />
      <div style={{ padding: "4px 16px 14px" }}>
        {tools.map((t, i) => (
          <div key={t.name} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 0", borderBottom: i === tools.length - 1 ? "none" : "1px solid var(--divider-soft)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span className={`dot ${t.on ? "dot-up" : "dot-off"}`} />
              <span className="t-mono" style={{ color: t.on ? "var(--ink)" : "var(--ink-60)" }}>{t.name}</span>
            </div>
            <span className="t-mono-sm" style={{ color: t.calls ? "var(--primary)" : "var(--ink-60)" }}>{t.calls}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function InspectorSkills({ onJump }) {
  return (
    <div className="card">
      <CardHeader title="技能命中" right={<button className="btn btn-ghost btn-sm" onClick={() => onJump("skills")}>→</button>} />
      <div className="card-body" style={{ paddingTop: 4, display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="dot dot-up" />
            <span className="t-mono">video-editor</span>
          </div>
          <span className="chip chip-info">脚本</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="dot dot-off" />
            <span className="t-mono" style={{ color: "var(--ink-60)" }}>wechat-style</span>
          </div>
          <span className="t-meta">REPL 不激活</span>
        </div>
      </div>
    </div>
  );
}

function InspectorMemory({ onJump }) {
  return (
    <div className="card">
      <CardHeader title="记忆命中" right={<button className="btn btn-ghost btn-sm" onClick={() => onJump("memory")}>→</button>} />
      <div className="card-body" style={{ paddingTop: 4, display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Icon name="doc" size={13} color="var(--primary)"/>
          <span className="t-mono-sm" style={{ color: "var(--ink)" }}>user/preferred_format.md</span>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Icon name="doc" size={13} color="var(--primary)"/>
          <span className="t-mono-sm" style={{ color: "var(--ink)" }}>project/jx-style-guide.md</span>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Icon name="doc" size={13} color="var(--ink-48)"/>
          <span className="t-mono-sm" style={{ color: "var(--ink-60)" }}>CLAUDE.md · 1,210 字</span>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { Chat });
