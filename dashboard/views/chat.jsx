/* Chat / Sessions — 真实接 /api/sessions + /chat SSE。 */

function Chat() {
  const [sessions, setSessions]   = React.useState([]);
  const [activeId, setActiveId]   = React.useState(null);
  const [messages, setMessages]   = React.useState([]);
  const [filter, setFilter]       = React.useState("all");
  const [composer, setComposer]   = React.useState("");
  const [streaming, setStreaming] = React.useState(false);
  const [streamText, setStreamText] = React.useState("");
  // 待审批 + 已结案的工具授权卡片（替代原 window.confirm）
  const [pendingApprovals, setPendingApprovals] = React.useState([]);
  const [resolvedApprovals, setResolvedApprovals] = React.useState([]);
  const streamCtrl = React.useRef(null);

  // 拉会话列表（5s 轮询）
  React.useEffect(() => {
    let alive = true;
    const fetchList = async () => {
      const r = await API.get("/api/sessions?limit=50");
      if (alive && !r.error) {
        const list = r.sessions || [];
        setSessions(list);
        if (!activeId && list.length > 0) setActiveId(list[0].id);
      }
    };
    fetchList();
    const id = setInterval(fetchList, 5000);
    return () => { alive = false; clearInterval(id); };
  }, [activeId]);

  // 拉某个会话的历史消息
  React.useEffect(() => {
    if (!activeId) { setMessages([]); setPendingApprovals([]); setResolvedApprovals([]); return; }
    setPendingApprovals([]);
    setResolvedApprovals([]);
    let alive = true;
    (async () => {
      const r = await API.get(`/api/sessions/${encodeURIComponent(activeId)}/messages`);
      if (alive && !r.error) setMessages(r.messages || []);
    })();
    return () => { alive = false; };
  }, [activeId]);

  const filtered = sessions.filter(s =>
    filter === "all" ||
    (filter === "repl"   && s.channel === "repl") ||
    (filter === "web"    && s.channel === "web")  ||
    (filter === "wechat" && s.channel === "wechat")
  );

  const active = sessions.find(s => s.id === activeId);

  // 把待审批的工具调用以聊天卡片形式插入会话流，不再弹浏览器原生 confirm。
  const askToolApproval = (approval) => {
    setPendingApprovals(list => list.some(a => a.id === approval.id) ? list : [...list, approval]);
  };

  const resolveApproval = async (approval, decision, scope) => {
    setPendingApprovals(list => list.filter(a => a.id !== approval.id));
    setResolvedApprovals(list => [...list, { approval, decision, scope, at: Date.now() }]);
    const r = await API.respondToolApproval(approval.id, decision, scope);
    if (r.error) {
      setMessages(m => [...m, { role: "assistant", content: `[工具审批提交失败] ${r.error}` }]);
    }
  };

  const send = () => {
    const text = composer.trim();
    if (!text || streaming) return;
    const sessionForSend = activeId && (!active || active.channel === "web") ? activeId : null;
    setMessages(m => [...m, { role: "user", content: text }]);
    setComposer("");
    setStreaming(true);
    setStreamText("");
    let buf = "";
    streamCtrl.current = API.chatStream({
      q: text,
      sessionId: sessionForSend,
      onSession: (sid) => {
        if (!sid) return;
        setActiveId(sid);
        setSessions(list => list.some(s => s.id === sid)
          ? list
          : [{ id: sid, channel: "web", calls: 0, input_tokens: 0, output_tokens: 0, cost_cny: 0, last_active_at: Date.now(), last_message: text }, ...list]);
      },
      onApproval: askToolApproval,
      onDelta: (chunk) => { buf += chunk; setStreamText(buf); },
      onDone:  () => {
        if (buf) setMessages(m => [...m, { role: "assistant", content: buf }]);
        setStreamText("");
        setStreaming(false);
        streamCtrl.current = null;
        // 触发会话列表刷新
        API.get("/api/sessions?limit=50").then(r => { if (!r.error) setSessions(r.sessions || []); });
      },
      onError: (msg) => {
        setMessages(m => [...m, { role: "assistant", content: `[错误] ${msg}` }]);
        setStreamText("");
        setStreaming(false);
        setPendingApprovals([]);
        streamCtrl.current = null;
      },
    });
  };

  const stop = () => {
    if (streamCtrl.current) streamCtrl.current.abort();
    streamCtrl.current = null;
    setStreaming(false);
    if (streamText) setMessages(m => [...m, { role: "assistant", content: streamText + " …[已中止]" }]);
    setStreamText("");
    setPendingApprovals([]);
  };

  const newSession = async () => {
    const r = await API.post("/api/sessions/new", { channel: "web" });
    if (r.error) { alert("新建失败：" + r.error); return; }
    setActiveId(r.session_id);
    setMessages([]);
    setSessions(list => list.some(s => s.id === r.session_id)
      ? list
      : [{ id: r.session_id, channel: r.channel || "web", calls: 0, input_tokens: 0, output_tokens: 0, cost_cny: 0, last_active_at: Date.now(), last_message: "" }, ...list]);
    // 刷新列表；新建会话未发生调用前可能还未落 DB，保留本地占位
    const lst = await API.get("/api/sessions?limit=50");
    if (!lst.error && (lst.sessions || []).length) setSessions(lst.sessions || []);
  };

  const deleteSession = async (sessionId) => {
    if (streaming && sessionId === activeId) {
      alert("当前会话正在生成，先停止再删除。");
      return;
    }
    const item = sessions.find(s => s.id === sessionId);
    const name = (item && item.last_message)
      ? `“${item.last_message.slice(0, 28)}${item.last_message.length > 28 ? "..." : ""}”`
      : sessionId.slice(0, 16);
    if (!window.confirm(`删除历史会话 ${name}？\n本地消息、工具调用和会话统计都会移除。`)) return;

    const r = await API.del(`/api/sessions/${encodeURIComponent(sessionId)}`);
    if (r.error) {
      alert("删除失败：" + r.error);
      return;
    }

    const next = sessions.filter(s => s.id !== sessionId);
    setSessions(next);
    if (activeId === sessionId) {
      setMessages([]);
      setActiveId(next[0]?.id || null);
    }

    const fresh = await API.get("/api/sessions?limit=50");
    if (!fresh.error) {
      const list = fresh.sessions || [];
      setSessions(list);
      if (activeId === sessionId) {
        setActiveId(list[0]?.id || null);
        setMessages([]);
      }
    }
  };

  const handleComposerKeyDown = (e) => {
    if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;
    e.preventDefault();
    if (streaming) stop();
    else send();
  };

  const totals = sessions.reduce((acc, s) => ({
    msgs: acc.msgs + (s.calls || 0),
    tok:  acc.tok + (s.input_tokens || 0) + (s.output_tokens || 0),
    cost: acc.cost + (s.cost_cny || 0),
  }), { msgs: 0, tok: 0, cost: 0 });

  return (
    <div data-screen-label="02 会话" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <PageHeader
        title="会话"
        sub={`${sessions.length} 条 · 累计 ${API.fmtNumber(totals.tok)} tokens · ￥${API.fmtCost(totals.cost)}`}
        actions={
          <>
            <Segmented value={filter} onChange={setFilter} options={[
              { id: "all",    label: "全部" },
              { id: "repl",   label: "REPL" },
              { id: "web",    label: "Web" },
              { id: "wechat", label: "微信" },
            ]} />
            <button className="btn btn-secondary" onClick={() => {
              const jsonl = sessions.map(s => JSON.stringify(s)).join("\n");
              API.download("sessions.jsonl", jsonl);
            }}><Icon name="download" size={13}/>导出</button>
            <button className="btn btn-primary" onClick={newSession}><Icon name="plus" size={13} color="#fff"/>新建会话</button>
          </>
        }
      />

      <div style={{
        display: "grid",
        gridTemplateColumns: "320px 1fr",
        gap: 16,
        padding: "16px 28px 24px",
        flex: 1,
        minHeight: 0,
        alignItems: "stretch",
      }}>
        {/* LEFT: session list */}
        <div className="card" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <div className="card-header" style={{ padding: "10px 14px" }}>
            <div className="search-wrap grow">
              <span className="search-icon"><Icon name="search" size={13} color="var(--ink-48)"/></span>
              <input className="search" placeholder="搜索会话 ID 或文本"/>
            </div>
          </div>
          <div style={{ overflowY: "auto", flex: 1 }}>
            {filtered.length === 0 ? (
              <div style={{ padding: 32, textAlign: "center", color: "var(--ink-60)" }} className="t-meta">暂无会话<br/>点击"新建会话"开始</div>
            ) : filtered.map(s => (
              <SessionListItem
                key={s.id}
                s={s}
                active={s.id === activeId}
                onClick={() => setActiveId(s.id)}
                onDelete={() => deleteSession(s.id)}
              />
            ))}
          </div>
        </div>

        {/* CENTER: conversation */}
        <div className="card" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <div className="card-header">
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              {active ? (
                <>
                  <span className={`chip ${active.channel === "wechat" ? "" : active.channel === "web" ? "chip-info" : "chip-success"}`}
                        style={active.channel === "wechat" ? { background: "rgba(193,60,123,0.10)", color: "#9c2f5f" } : {}}>
                    {active.channel}
                  </span>
                  <span className="t-mono-strong" style={{ color: "var(--ink)" }}>{active.id.slice(0, 16)}</span>
                  <span className="t-meta" style={{ color: "var(--ink-60)" }}>
                    {active.calls} 次调用 · {API.fmtNumber((active.input_tokens || 0) + (active.output_tokens || 0))} tok · ￥{API.fmtCost(active.cost_cny)}
                  </span>
                </>
              ) : (
                <span className="t-mono-strong" style={{ color: "var(--ink-60)" }}>未选择会话</span>
              )}
            </div>
          </div>

          {/* Transcript */}
          <div style={{ flex: 1, overflowY: "auto", padding: "20px 28px" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
              {messages.length === 0 && !streaming && (
                <div className="t-meta" style={{ textAlign: "center", padding: 40 }}>这个会话还没有消息。下方输入框开始聊天 ↓</div>
              )}
              {messages.map((m, i) => <Bubble key={i} role={m.role} text={m.content} />)}
              {streaming && <Bubble role="assistant" text={streamText} streaming t="正在生成" />}
              {resolvedApprovals.map((r, i) => (
                <ApprovalCard key={`r-${r.approval.id}-${i}`} approval={r.approval} resolved={r} />
              ))}
              {pendingApprovals.map((a) => (
                <ApprovalCard key={`p-${a.id}`} approval={a} onResolve={(decision, scope) => resolveApproval(a, decision, scope)} />
              ))}
            </div>
          </div>

          {/* Composer */}
          <div style={{ borderTop: "1px solid var(--hairline)", padding: "12px 16px", background: "var(--pearl)" }}>
            <div style={{
              border: "1px solid var(--hairline)",
              borderRadius: 12,
              background: "var(--canvas)",
              padding: "10px 14px",
              display: "flex",
              flexDirection: "column",
              gap: 6,
            }}>
              <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
                <textarea
                  rows={2}
                  style={{
                    flex: 1, resize: "none", border: "none", outline: "none",
                    background: "transparent", fontFamily: "var(--font-mono)",
                    fontSize: 14, lineHeight: 1.5, color: "var(--ink)", padding: "4px 0",
                  }}
                  placeholder="说人话，Enter 发送，Shift+Enter 换行"
                  value={composer}
                  onChange={e => setComposer(e.target.value)}
                  onKeyDown={handleComposerKeyDown}
                />
                <button
                  className="btn-icon"
                  onClick={streaming ? stop : send}
                  disabled={!streaming && !composer.trim()}
                  title={streaming ? "停止生成（Enter）" : "发送（Enter）"}
                  style={{ color: streaming ? "var(--danger)" : "var(--ink-60)", fontSize: 14, lineHeight: 1, padding: 4 }}>
                  {streaming ? <Icon name="stop" size={13} /> : "↵"}
                </button>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span className="t-meta" style={{ color: "var(--ink-60)" }}>web 通道</span>
                <span className="t-meta">流式 · SSE</span>
              </div>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}

function SessionListItem({ s, active, onClick, onDelete }) {
  const chColor = s.channel === "wechat" ? "#9c2f5f" : s.channel === "web" ? "var(--primary)" : "var(--success-fg)";
  const tokens = (s.input_tokens || 0) + (s.output_tokens || 0);
  return (
    <div
      onClick={onClick}
      style={{
        padding: "12px 14px",
        borderLeft: active ? "3px solid var(--primary)" : "3px solid transparent",
        borderBottom: "1px solid var(--divider-soft)",
        background: active ? "var(--primary-soft)" : "transparent",
        cursor: "pointer",
      }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <span className="t-mono-strong" title={s.id} style={{ color: active ? "var(--primary)" : "var(--ink)", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>{s.id.slice(0, 16)}</span>
        <div style={{ display: "flex", alignItems: "center", gap: 6, flex: "0 0 auto" }}>
          <span className="t-meta">{API.relTime(s.last_active_at)}</span>
          <button
            className="btn-icon"
            title="删除会话"
            style={{ width: 24, height: 24 }}
            onClick={(e) => { e.stopPropagation(); onDelete && onDelete(); }}>
            <Icon name="trash" size={12} color="var(--danger)" />
          </button>
        </div>
      </div>
      <div className="t-row" style={{ color: "var(--ink)", marginTop: 6, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.last_message || "—"}</div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8 }}>
        <span className="t-meta" style={{ color: chColor, fontWeight: 500 }}>{s.channel}</span>
        <span className="t-meta">{s.calls} 次</span>
        <span className="t-meta">·</span>
        <span className="t-meta">{(tokens / 1000).toFixed(1)}k tok</span>
        <span className="t-meta">·</span>
        <span className="t-meta">￥{API.fmtCost(s.cost_cny)}</span>
      </div>
    </div>
  );
}

function ApprovalCard({ approval, onResolve, resolved }) {
  const toolName = approval.canonical_name || approval.tool_name || "工具";
  const danger = approval.danger;
  const dangerColor = danger === "critical" ? "var(--danger)"
                    : danger === "high"     ? "#c4660a"
                    : danger === "moderate" ? "#9c7700"
                    : "var(--ink-60)";
  const headerBg = resolved
    ? "var(--pearl)"
    : danger === "critical" || danger === "high"
      ? "rgba(193,60,60,0.06)"
      : "var(--primary-soft)";

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <span style={{
          width: 22, height: 22, borderRadius: "50%",
          background: "var(--ink-60)", color: "#fff",
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          fontSize: 10, fontWeight: 600,
        }}>权</span>
        <span className="t-row-strong" style={{ color: "var(--ink)" }}>工具授权</span>
        <span className="t-meta" style={{ color: "var(--ink-60)" }}>
          {resolved
            ? (resolved.decision === "allow" ? `已允许（${resolved.scope}）` : "已拒绝")
            : "等待你的确认"}
        </span>
      </div>
      <div style={{
        marginLeft: 30,
        padding: "12px 14px",
        background: headerBg,
        border: "1px solid var(--hairline)",
        borderRadius: 10,
        fontFamily: "var(--font-text)",
        fontSize: 13.5,
        lineHeight: 1.5,
        color: "var(--ink)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
          <span style={{ fontWeight: 600 }}>{toolName}</span>
          {approval.tool_name && approval.tool_name !== toolName && (
            <span className="t-meta" style={{ color: "var(--ink-60)" }}>· 运行时 {approval.tool_name}</span>
          )}
          {danger && (
            <span className="t-meta" style={{ color: dangerColor, fontWeight: 500 }}>· {danger}</span>
          )}
          {approval.matched_rule && (
            <span className="t-meta" style={{ color: "var(--ink-60)" }}>· {approval.matched_rule}</span>
          )}
        </div>
        {approval.arguments_preview && (
          <pre style={{
            margin: 0, padding: "8px 10px",
            background: "var(--canvas)", border: "1px solid var(--hairline)",
            borderRadius: 6, fontFamily: "var(--font-mono)", fontSize: 12,
            whiteSpace: "pre-wrap", wordBreak: "break-all", color: "var(--ink)",
          }}>{approval.arguments_preview}</pre>
        )}
        {!resolved && (
          <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
            <button className="btn btn-primary" onClick={() => onResolve("allow", "once")}>允许本次</button>
            <button className="btn btn-secondary" onClick={() => onResolve("allow", "session")}>允许本会话</button>
            <button className="btn btn-secondary" onClick={() => onResolve("allow", "permanent")}>始终允许</button>
            <button className="btn btn-secondary" style={{ color: "var(--danger)" }} onClick={() => onResolve("deny", "once")}>拒绝</button>
          </div>
        )}
      </div>
    </div>
  );
}

function Bubble({ role, text, streaming, t }) {
  const isAgent = role === "assistant" || role === "agent";
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
        <span className="t-row-strong" style={{ color: "var(--ink)" }}>{isAgent ? "三十六贱笑" : "你"}</span>
        {t && <span className="t-meta" style={{ color: "var(--ink-60)" }}>{t}</span>}
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
          fontSize: 14, lineHeight: 1.55, letterSpacing: "-0.012em",
          color: "var(--ink)", whiteSpace: "pre-wrap",
        }}>
          {text || "—"}
          {streaming && <span className="blink-cursor" style={{ color: "var(--primary)", marginLeft: 2 }}>▮</span>}
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { Chat });
