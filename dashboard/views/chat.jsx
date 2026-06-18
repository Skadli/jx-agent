/* Chat / Sessions — 真实接 /api/sessions + /chat SSE。
 * timeline 统一保存所有事件（user/assistant/tool/approval），按发生顺序渲染，
 * 解决"工具调用 / 审批卡片固定在底部"和"刷新后工具调用消失"的两个 bug。
 */

// 与后端保持一致；见 src/sanshiliu/foundation/config.py 的
// multimodal_max_images_per_turn / multimodal_max_image_bytes
const IMG_MIME_WHITELIST = ["image/jpeg", "image/png", "image/webp"];
const IMG_MAX_PER_TURN   = 4;
const IMG_MAX_BYTES      = 5 * 1024 * 1024;

function Chat() {
  const [sessions, setSessions]   = React.useState([]);
  const [activeId, setActiveId]   = React.useState(null);
  // 统一时间线；元素形如:
  //   { kind: "user", content, images?: [dataUri] }
  //   { kind: "assistant", content }
  //   { kind: "tool", id, name, arguments, result, is_error }
  //   { kind: "approval", id, payload, decision?, scope? }
  const [timeline, setTimeline]   = React.useState([]);
  const [filter, setFilter]       = React.useState("all");
  const [composer, setComposer]   = React.useState("");
  const [streaming, setStreaming] = React.useState(false);
  const [streamText, setStreamText] = React.useState("");
  const [pendingImages, setPendingImages] = React.useState([]);
  const [isDragOver, setIsDragOver] = React.useState(false);
  const streamCtrl = React.useRef(null);
  const fileInputRef = React.useRef(null);
  const dropZoneRef = React.useRef(null);

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

  // 拉某个会话的历史消息并重建 timeline
  React.useEffect(() => {
    if (!activeId) { setTimeline([]); return; }
    let alive = true;
    (async () => {
      const r = await API.get(`/api/sessions/${encodeURIComponent(activeId)}/messages`);
      if (alive && !r.error) setTimeline(buildTimelineFromMessages(r.messages || []));
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

  // 工具审批：直接插入 timeline 末尾，按发生顺序显示
  const askToolApproval = (approval) => {
    setTimeline(t => t.some(it => it.kind === "approval" && it.id === approval.id)
      ? t
      : [...t, { kind: "approval", id: approval.id, payload: approval }]);
  };

  const resolveApproval = async (approval, decision, scope) => {
    setTimeline(t => t.map(it =>
      (it.kind === "approval" && it.id === approval.id)
        ? { ...it, decision, scope, resolvedAt: Date.now() }
        : it
    ));
    const r = await API.respondToolApproval(approval.id, decision, scope);
    if (r.error) {
      setTimeline(t => [...t, { kind: "assistant", content: `[工具审批提交失败] ${r.error}` }]);
    }
  };

  const send = () => {
    const text = composer.trim();
    // 只发已读完 base64 的图；未读完的（极短窗口内点发送）先拦掉
    const imgs = pendingImages.filter(p => p.dataUri).map(p => p.dataUri);
    if (streaming) return;
    if (!text && imgs.length === 0) return;
    if (pendingImages.length > 0 && imgs.length < pendingImages.length) {
      alert("还有图片正在读取中，请稍候再发");
      return;
    }
    const sessionForSend = activeId && (!active || active.channel === "web") ? activeId : null;
    setTimeline(t => [...t, { kind: "user", content: text, images: imgs }]);
    setComposer("");
    // 图已捕进 imgs 并随消息发出、也已落进 user 气泡——立刻清空输入框附件区，
    // 否则整段流式期间图一直挂在输入框（bug）。流式中无法再加图（按钮禁用 + canAcceptMore 拦截）。
    setPendingImages([]);
    setStreaming(true);
    setStreamText("");
    let buf = "";
    streamCtrl.current = API.chatStream({
      q: text,
      sessionId: sessionForSend,
      images: imgs,
      onSession: (sid) => {
        if (!sid) return;
        setActiveId(sid);
        setSessions(list => list.some(s => s.id === sid)
          ? list
          : [{ id: sid, channel: "web", calls: 0, input_tokens: 0, output_tokens: 0, cost_cny: 0, last_active_at: Date.now(), last_message: text }, ...list]);
      },
      onApproval: askToolApproval,
      onMsgBreak: () => {
        // <MSG> 段边界：把当前段定型为一条独立气泡，后续 delta 进新气泡
        if (buf.trim()) setTimeline(t => [...t, { kind: "assistant", content: buf }]);
        buf = "";
        setStreamText("");
      },
      onDelta: (chunk) => { buf += chunk; setStreamText(buf); },
      onDone:  () => {
        if (buf) setTimeline(t => [...t, { kind: "assistant", content: buf }]);
        setStreamText("");
        setStreaming(false);
        streamCtrl.current = null;
        // 触发会话列表刷新 + 重拉本会话历史消息（取回工具调用记录）
        API.get("/api/sessions?limit=50").then(r => { if (!r.error) setSessions(r.sessions || []); });
        if (activeId || sessionForSend) {
          const sid = activeId || sessionForSend;
          API.get(`/api/sessions/${encodeURIComponent(sid)}/messages`).then(r => {
            if (!r.error) setTimeline(buildTimelineFromMessages(r.messages || []));
          });
        }
      },
      onError: (msg) => {
        setTimeline(t => [...t, { kind: "assistant", content: `[错误] ${msg}` }]);
        setStreamText("");
        setStreaming(false);
        streamCtrl.current = null;
      },
    });
  };

  const stop = () => {
    if (streamCtrl.current) streamCtrl.current.abort();
    streamCtrl.current = null;
    setStreaming(false);
    if (streamText) setTimeline(t => [...t, { kind: "assistant", content: streamText + " …[已中止]" }]);
    setStreamText("");
  };

  const newSession = async () => {
    const r = await API.post("/api/sessions/new", { channel: "web" });
    if (r.error) { alert("新建失败：" + r.error); return; }
    setActiveId(r.session_id);
    setTimeline([]);
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
      setTimeline([]);
      setActiveId(next[0]?.id || null);
    }

    const fresh = await API.get("/api/sessions?limit=50");
    if (!fresh.error) {
      const list = fresh.sessions || [];
      setSessions(list);
      if (activeId === sessionId) {
        setActiveId(list[0]?.id || null);
        setTimeline([]);
      }
    }
  };

  const handleComposerKeyDown = (e) => {
    if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;
    e.preventDefault();
    if (streaming) stop();
    else send();
  };

  const openFilePicker = () => {
    if (fileInputRef.current) fileInputRef.current.click();
  };

  // 共享的 File[] 校验 + 读取逻辑：被文件选择器 / 拖拽 / 粘贴三个入口复用
  const addFiles = (files) => {
    const list = Array.from(files || []);
    if (list.length === 0) return;

    setPendingImages(prev => {
      const next = prev.slice();
      for (const f of list) {
        if (next.length >= IMG_MAX_PER_TURN) {
          alert(`最多附 ${IMG_MAX_PER_TURN} 张图，已忽略剩余文件`);
          break;
        }
        if (!IMG_MIME_WHITELIST.includes(f.type)) {
          alert(`不支持的文件类型：${f.type || "未知"}（仅支持 jpeg/png/webp）`);
          continue;
        }
        if (f.size > IMG_MAX_BYTES) {
          alert(`图片过大：${f.name} ${(f.size/1024/1024).toFixed(2)}MB > 5MB`);
          continue;
        }
        const reader = new FileReader();
        const entry = {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          dataUri: "",
          name: f.name,
          sizeBytes: f.size,
        };
        next.push(entry);
        reader.onload = () => {
          const uri = String(reader.result || "");
          setPendingImages(curr => curr.map(p => p.id === entry.id ? { ...p, dataUri: uri } : p));
        };
        reader.onerror = () => {
          alert(`读取失败：${f.name}`);
          setPendingImages(curr => curr.filter(p => p.id !== entry.id));
        };
        reader.readAsDataURL(f);
      }
      return next;
    });
  };

  const handleFilesPicked = (e) => {
    const files = Array.from(e.target.files || []);
    // 清空 input value 以便下次选同名文件仍能触发 onChange
    e.target.value = "";
    addFiles(files);
  };

  const canAcceptMore = () => !streaming && pendingImages.length < IMG_MAX_PER_TURN;

  const handleDragEnter = (e) => {
    if (!canAcceptMore()) return;
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(true);
  };

  const handleDragOver = (e) => {
    if (!canAcceptMore()) return;
    // 必须每次 preventDefault 否则浏览器拒绝 drop
    e.preventDefault();
    e.stopPropagation();
    if (!isDragOver) setIsDragOver(true);
  };

  const handleDragLeave = (e) => {
    // 只在离开容器本身（非内部子元素切换）时置 false，避免子元素 drag 触发抖动
    if (e.currentTarget === e.target) {
      setIsDragOver(false);
      return;
    }
    const related = e.relatedTarget;
    if (!related || !(e.currentTarget instanceof Node) || !(related instanceof Node) || !e.currentTarget.contains(related)) {
      setIsDragOver(false);
    }
  };

  const handleDrop = (e) => {
    if (!canAcceptMore()) return;
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
    const dropped = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
    const images = dropped.filter(f => f.type && f.type.startsWith("image/"));
    if (images.length > 0) addFiles(images);
  };

  const handlePaste = (e) => {
    if (!canAcceptMore()) return;
    const items = (e.clipboardData && e.clipboardData.items) || [];
    const files = [];
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.kind === "file" && item.type && item.type.startsWith("image/")) {
        const f = item.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length === 0) return; // 没图片项不拦截，让默认粘贴文本继续
    e.preventDefault();
    addFiles(files);
  };

  const removePendingImage = (id) => {
    setPendingImages(prev => prev.filter(p => p.id !== id));
  };

  const openImageInNewTab = (dataUri) => {
    const w = window.open();
    if (w) w.document.write(`<img src="${dataUri}" style="max-width:100%;height:auto"/>`);
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
        gridTemplateColumns: "minmax(240px, 320px) minmax(0, 1fr)",
        gap: 16,
        padding: "16px 28px 24px",
        flex: 1,
        minHeight: 0,
        minWidth: 0,
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

          {/* Transcript — 按 timeline 顺序渲染 */}
          <div style={{ flex: 1, overflowY: "auto", padding: "20px 28px" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
              {timeline.length === 0 && !streaming && (
                <div className="t-meta" style={{ textAlign: "center", padding: 40 }}>这个会话还没有消息。下方输入框开始聊天 ↓</div>
              )}
              {timeline.map((it, i) => {
                if (it.kind === "user" || it.kind === "assistant") {
                  return <Bubble key={i} role={it.kind} text={it.content} images={it.images} />;
                }
                if (it.kind === "tool") {
                  return <ToolEventCard key={i} event={it} />;
                }
                if (it.kind === "approval") {
                  return (
                    <ApprovalCard
                      key={`a-${it.id}`}
                      approval={it.payload}
                      resolved={it.decision ? { decision: it.decision, scope: it.scope } : null}
                      onResolve={(decision, scope) => resolveApproval(it.payload, decision, scope)} />
                  );
                }
                return null;
              })}
              {streaming && <Bubble role="assistant" text={streamText} streaming t="正在生成" />}
            </div>
          </div>

          {/* Composer */}
          <div style={{ borderTop: "1px solid var(--hairline)", padding: "12px 16px", background: "var(--pearl)" }}>
            <div
              ref={dropZoneRef}
              onDragEnter={handleDragEnter}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              style={{
                position: "relative",
                border: isDragOver ? "2px dashed var(--primary)" : "1px solid var(--hairline)",
                borderRadius: 12,
                background: "var(--canvas)",
                padding: isDragOver ? "9px 13px" : "10px 14px",
                display: "flex",
                flexDirection: "column",
                gap: 6,
              }}>
              {isDragOver && (
                <div style={{
                  position: "absolute",
                  top: 6, left: 0, right: 0,
                  textAlign: "center",
                  color: "var(--primary)",
                  fontSize: 13,
                  pointerEvents: "none",
                }}>释放以附加图片</div>
              )}
              {pendingImages.length > 0 && (
                <div style={{
                  display: "flex", flexWrap: "wrap", gap: 8,
                  padding: "6px 0", borderBottom: "1px dashed var(--hairline)",
                }}>
                  {pendingImages.map(p => (
                    <div key={p.id} style={{
                      display: "flex", alignItems: "center", gap: 6,
                      padding: "4px 6px", background: "var(--pearl)",
                      border: "1px solid var(--hairline)", borderRadius: 8,
                    }}>
                      {p.dataUri ? (
                        <img
                          src={p.dataUri}
                          onClick={() => openImageInNewTab(p.dataUri)}
                          style={{
                            width: 32, height: 32, objectFit: "cover",
                            borderRadius: 4, cursor: "pointer",
                          }}
                          title="点击查看大图"
                          alt={p.name}
                        />
                      ) : (
                        <div style={{
                          width: 32, height: 32, borderRadius: 4,
                          background: "var(--canvas)", border: "1px dashed var(--hairline)",
                          display: "inline-flex", alignItems: "center", justifyContent: "center",
                          fontSize: 10, color: "var(--ink-60)",
                        }}>…</div>
                      )}
                      <span className="t-meta" style={{
                        maxWidth: 140, overflow: "hidden",
                        textOverflow: "ellipsis", whiteSpace: "nowrap",
                        color: "var(--ink-80)",
                      }} title={`${p.name} · ${(p.sizeBytes/1024).toFixed(1)}KB`}>{p.name}</span>
                      <button
                        className="btn-icon"
                        onClick={() => removePendingImage(p.id)}
                        title="移除"
                        style={{
                          width: 18, height: 18, padding: 0,
                          color: "var(--ink-60)", fontSize: 12, lineHeight: 1,
                        }}>✕</button>
                    </div>
                  ))}
                </div>
              )}
              <input
                ref={fileInputRef}
                type="file"
                accept="image/jpeg,image/png,image/webp"
                multiple
                style={{ display: "none" }}
                onChange={handleFilesPicked}
              />
              <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
                <textarea
                  rows={2}
                  style={{
                    flex: 1, resize: "none", border: "none", outline: "none",
                    background: "transparent", fontFamily: "var(--font-mono)",
                    fontSize: 14, lineHeight: 1.5, color: "var(--ink)", padding: "4px 0",
                  }}
                  placeholder={"说人话，Enter 发送 / Shift+Enter 换行 / 输入 /help 看可用命令\n拖入或粘贴图片可附图"}
                  value={composer}
                  onChange={e => setComposer(e.target.value)}
                  onKeyDown={handleComposerKeyDown}
                  onPaste={handlePaste}
                />
                <button
                  className="btn-icon"
                  onClick={streaming ? stop : send}
                  disabled={!streaming && !composer.trim() && pendingImages.length === 0}
                  title={streaming ? "停止生成（Enter）" : "发送（Enter）"}
                  style={{ color: streaming ? "var(--danger)" : "var(--ink-60)", fontSize: 14, lineHeight: 1, padding: 4 }}>
                  {streaming ? <Icon name="stop" size={13} /> : <Icon name="send" size={14} />}
                </button>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <button
                    className="btn btn-secondary"
                    onClick={openFilePicker}
                    disabled={streaming || pendingImages.length >= IMG_MAX_PER_TURN}
                    title={`添加图片（≤ ${IMG_MAX_PER_TURN} 张，单张 ≤ 5MB）`}
                    style={{
                      height: 28, padding: "0 10px", fontSize: 12,
                      color: "var(--ink-80)",
                    }}>
                    <Icon name="image" size={13} />
                    <span>添加图片</span>
                    {pendingImages.length > 0 && (
                      <span className="t-meta" style={{ color: "var(--ink-60)", marginLeft: 2 }}>
                        {pendingImages.length}/{IMG_MAX_PER_TURN}
                      </span>
                    )}
                  </button>
                  <span className="t-meta" style={{ color: "var(--ink-60)" }}>web 通道</span>
                </div>
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
  const dangerColor = danger === "critical"  ? "var(--danger)"
                    : danger === "dangerous" ? "#c4660a"
                    : danger === "moderate"  ? "#9c7700"
                    : "var(--ink-60)";
  const headerBg = resolved
    ? "var(--pearl)"
    : danger === "critical" || danger === "dangerous"
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

/* 历史图片懒加载：data: URI 直接渲染；/api 引用走鉴权 fetch → blob（裸 <img> 不带 token 会 401），
 * 并用 IntersectionObserver 滚到视口附近(200px)才拉。卸载时 revoke object URL 防泄漏。 */
function AuthImage({ src, alt, title, style, onClick }) {
  const isRef = typeof src === "string" && src.startsWith("/api/");
  const [resolved, setResolved] = React.useState(isRef ? "" : src);
  const [visible, setVisible] = React.useState(!isRef);
  const holderRef = React.useRef(null);

  React.useEffect(() => {
    if (visible || !holderRef.current) return undefined;
    const el = holderRef.current;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) { setVisible(true); io.disconnect(); }
    }, { rootMargin: "200px" });
    io.observe(el);
    return () => io.disconnect();
  }, [visible]);

  React.useEffect(() => {
    if (!isRef || !visible) return undefined;
    let alive = true;
    let objUrl = "";
    API.blobUrl(src).then((u) => {
      if (alive) { objUrl = u; setResolved(u); }
      else if (u) URL.revokeObjectURL(u);
    });
    return () => { alive = false; if (objUrl) URL.revokeObjectURL(objUrl); };
  }, [src, visible, isRef]);

  if (!resolved) {
    return (
      <div ref={holderRef} title={title} style={{
        ...style, display: "inline-flex", alignItems: "center",
        justifyContent: "center", background: "var(--canvas)",
        color: "var(--ink-60)", fontSize: 10,
      }}>图…</div>
    );
  }
  // 把已解析好的 url（blob/data）回传给 onClick，省得看大图时再 fetch 一遍
  return <img src={resolved} alt={alt} title={title} style={style}
    onClick={onClick ? () => onClick(resolved) : undefined} />;
}

function Bubble({ role, text, images, streaming, t }) {
  const isAgent = role === "assistant" || role === "agent";
  const imgs = Array.isArray(images) ? images.slice(0, 4) : [];
  // AuthImage 传入的是已解析好的 url（data: 或缩略图复用的 blob），无需再 fetch
  const openImg = (viewUrl) => {
    if (!viewUrl) return;
    const w = window.open();
    if (w) w.document.write(`<img src="${viewUrl}" style="max-width:100%;height:auto"/>`);
  };
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
        {imgs.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: text ? 8 : 0 }}>
            {imgs.map((url, i) => (
              <AuthImage
                key={i}
                src={url}
                onClick={openImg}
                style={{
                  width: 64, height: 64, objectFit: "cover",
                  borderRadius: 6, border: "1px solid var(--hairline)",
                  cursor: "pointer",
                }}
                title="点击查看大图"
                alt={`图 ${i + 1}`}
              />
            ))}
          </div>
        )}
        {(text || imgs.length === 0 || streaming) && (
          <div style={{
            fontFamily: "var(--font-text)",
            fontSize: 14, lineHeight: 1.55, letterSpacing: "-0.012em",
            color: "var(--ink)", whiteSpace: "pre-wrap",
          }}>
            {text || (imgs.length > 0 ? "" : "—")}
            {streaming && <span className="blink-cursor" style={{ color: "var(--primary)", marginLeft: 2 }}>▮</span>}
          </div>
        )}
      </div>
    </div>
  );
}

/* 按 <MSG> 把一段 assistant 文本拆成多条；与后端 foundation/msg_split.py 对齐：
 * 三反引号代码块内的 <MSG> 不拆；段首尾 trim；空段过滤；无 <MSG> 时整段单条返回。 */
function splitOnMsg(text, sentinel = "<MSG>") {
  if (typeof text !== "string" || !text.trim()) return [];
  const FENCE = "```";
  const out = [];
  let cur = text;
  while (true) {
    let pos = -1, p = 0, inCode = false;
    while (p < cur.length) {
      if (cur.startsWith(FENCE, p)) { inCode = !inCode; p += FENCE.length; continue; }
      if (!inCode && cur.startsWith(sentinel, p)) { pos = p; break; }
      p++;
    }
    if (pos < 0) break;
    const seg = cur.slice(0, pos).trim();
    if (seg) out.push(seg);
    cur = cur.slice(pos + sentinel.length);
  }
  const rest = cur.trim();
  if (rest) out.push(rest);
  return out.length ? out : [text.trim()];
}

/**
 * 把 /api/sessions/{id}/messages 返回的扁平消息流，
 * 重建为 timeline 的顺序事件：user / assistant / tool。
 * - assistant 的 tool_calls 拆成多个 pending tool 事件
 * - 后续 role=tool 按 tool_call_id 回填 result
 */
function buildTimelineFromMessages(msgs) {
  const items = [];
  const idMap = {};
  for (const m of msgs) {
    const role = m.role;
    if (role === "user") {
      // content 可能是 str（Phase 1-9）或 list-of-parts（Phase 10 多模态历史）
      if (Array.isArray(m.content)) {
        let contentText = "";
        const imageUrls = [];
        for (const part of m.content) {
          if (!part || typeof part !== "object") continue;
          if (part.type === "text" && typeof part.text === "string") {
            contentText += (contentText ? "\n" : "") + part.text;
          } else if (part.type === "image_url") {
            const url = part.image_url && part.image_url.url;
            if (typeof url === "string") imageUrls.push(url);
          }
        }
        items.push({ kind: "user", content: contentText, images: imageUrls });
      } else {
        items.push({ kind: "user", content: m.content || "" });
      }
    } else if (role === "assistant") {
      const calls = Array.isArray(m.tool_calls) ? m.tool_calls : [];
      // 带 tool_calls 的 assistant.content 是 preamble/状态，**不**作为答案气泡展示——与 live 流式
      // 一致（引擎已不再把它推给用户），否则回放时会出现"调用工具前后双答"。只有不带 tool_calls 的
      // assistant（最终答案）才把 content 渲染成气泡；tool 调用卡片照常展示。content 原文含字面 <MSG>，
      // 按段拆成多条气泡（与后端发送一致）。
      if (m.content && calls.length === 0) {
        for (const seg of splitOnMsg(m.content)) {
          items.push({ kind: "assistant", content: seg });
        }
      }
      for (const tc of calls) {
        const fn = tc.function || {};
        const item = {
          kind: "tool",
          id: tc.id,
          name: fn.name || tc.name || "tool",
          arguments: typeof fn.arguments === "string" ? fn.arguments : JSON.stringify(fn.arguments || {}),
          result: null,
          is_error: false,
        };
        items.push(item);
        if (tc.id) idMap[tc.id] = item;
      }
    } else if (role === "tool") {
      const target = m.tool_call_id ? idMap[m.tool_call_id] : null;
      if (target) {
        target.result = m.content || "";
      } else {
        items.push({
          kind: "tool",
          id: m.tool_call_id || `orphan-${items.length}`,
          name: m.name || "tool",
          arguments: "",
          result: m.content || "",
          is_error: false,
        });
      }
    }
  }
  return items;
}

/* 工具调用卡片：参数 + 结果，与 user/assistant 同级穿插显示。 */
function ToolEventCard({ event }) {
  const [open, setOpen] = React.useState(false);
  const pending = event.result == null;
  const preview = event.arguments
    ? (event.arguments.length > 80 ? event.arguments.slice(0, 80) + "…" : event.arguments)
    : "";
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <span style={{
          width: 22, height: 22, borderRadius: "50%",
          background: "var(--primary-soft-2)", color: "var(--primary)",
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          fontSize: 10, fontWeight: 600,
        }}>⚙</span>
        <span className="t-row-strong" style={{ color: "var(--ink)" }}>工具调用</span>
        <span className="t-mono-sm" style={{ color: "var(--primary)" }}>{event.name}</span>
        {pending
          ? <span className="t-meta" style={{ color: "var(--ink-60)" }}>· 执行中…</span>
          : event.is_error
            ? <span className="chip chip-danger" style={{ fontSize: 10 }}>错误</span>
            : <span className="chip chip-success" style={{ fontSize: 10 }}>ok</span>}
      </div>
      <div style={{
        marginLeft: 30,
        padding: "10px 12px",
        background: "var(--pearl)",
        border: "1px solid var(--hairline)",
        borderRadius: 10,
        fontFamily: "var(--font-mono)",
        fontSize: 12,
        lineHeight: 1.55,
        color: "var(--ink-80)",
      }}>
        {preview && (
          <div style={{ color: "var(--ink-60)", marginBottom: pending ? 0 : 6 }}>
            <span style={{ color: "var(--ink-48)" }}>args: </span>{preview}
          </div>
        )}
        {!pending && (
          <div>
            <div
              onClick={() => setOpen(o => !o)}
              style={{ cursor: "pointer", color: "var(--ink-60)", marginBottom: open ? 6 : 0, userSelect: "none" }}>
              <span style={{ color: "var(--ink-48)" }}>result {open ? "▾" : "▸"} </span>
              {!open && <span>{(event.result || "").slice(0, 80)}{(event.result || "").length > 80 ? "…" : ""}</span>}
            </div>
            {open && (
              <pre style={{
                margin: 0, padding: "8px 10px",
                background: "var(--canvas)", border: "1px solid var(--hairline)",
                borderRadius: 6, maxHeight: 260, overflow: "auto",
                whiteSpace: "pre-wrap", wordBreak: "break-all",
                fontFamily: "var(--font-mono)", fontSize: 11.5, color: "var(--ink)",
              }}>{event.result}</pre>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

Object.assign(window, { Chat });
