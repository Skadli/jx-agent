/* Gacha — 抽卡·人生卡池（替换原成长视图；设计见 抽卡平台-设计方案.md §7）。
 *   ① 顶部当前真身条：/api/gacha/active + 一键回本源。
 *   ② 锻造台：世界类型格（/api/gacha/genres）+ 创意度滑杆 + 自定义补充 → POST /api/gacha/draw。
 *   ③ 锻造直播：fetch ReadableStream 消费 SSE（card_created、chapter_start、chapter_done、skill_installed、rarity、done、error）。
 *   ④ 卡册网格：稀有度描边（N 灰 / R 蓝 / SR 紫 / SSR 金）+ 状态徽标；点卡看详情。
 *   ⑤ 卡详情：种子 + 评级评语 + 逐章时间轴（传记/汇报/习得/人格快照按需拉）+ 操作
 *      （转生[二次确认，全渠道生效] / 续锻 / 导出 JSON / 删除[创始卡禁删]）。
 * 无构建步：浏览器内 Babel-React，挂全局函数，约定同其它 views。
 */

/* 稀有度视觉表：边框 / 文字 / 底色；空 grade（未评级 / 未锻满）走 plain。 */
const RARITY_STYLES = {
  SSR: { border: "#d4a017", color: "#9a6a00", bg: "rgba(212,160,23,0.08)" },
  SR:  { border: "#8e5cd9", color: "#6b3fa0", bg: "rgba(142,92,217,0.08)" },
  R:   { border: "#3b82d4", color: "#1f5fa8", bg: "rgba(59,130,212,0.08)" },
  N:   { border: "var(--ink-48)", color: "var(--ink-60)", bg: "transparent" },
};
function rarityStyle(grade) {
  return RARITY_STYLES[grade] || { border: "var(--hairline)", color: "var(--ink-60)", bg: "transparent" };
}

const STATUS_LABEL = { forging: "锻造中", paused: "未锻满", complete: "已定格", error: "出错" };

/* fetch + ReadableStream 消费锻造 SSE；每帧 JSON 反序列化后回调 onEvent({type, ...})。
 * 非 SSE 响应（403/409/429/400 的 JSON 错误）转成一条 error 事件。返回 {abort}。 */
function forgeStream(path, body, onEvent, onClose) {
  const ctrl = new AbortController();
  (async () => {
    try {
      const headers = { "Content-Type": "application/json" };
      const t = API.token();
      if (t) headers["X-Dashboard-Token"] = t;
      const resp = await fetch(path, {
        method: "POST", headers, body: JSON.stringify(body || {}), signal: ctrl.signal,
      });
      const ctype = resp.headers.get("Content-Type") || "";
      if (!ctype.includes("text/event-stream")) {
        const data = await resp.json().catch(() => ({}));
        onEvent({ type: "error", message: data.error || `HTTP ${resp.status}` });
        return;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "", currentEvent = "message", dataLines = [];
      const flush = () => {
        if (dataLines.length === 0) { currentEvent = "message"; return; }
        const raw = dataLines.join("\n");
        dataLines = [];
        let payload;
        try { payload = JSON.parse(raw); } catch (e) { payload = { message: raw }; }
        if (typeof payload !== "object" || payload === null) payload = { message: String(raw) };
        onEvent({ ...payload, type: payload.type || currentEvent });
        currentEvent = "message";
      };
      while (true) {
        const { value, done } = await reader.read();
        if (done) { flush(); break; }
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, idx).replace(/\r$/, "");
          buffer = buffer.slice(idx + 1);
          if (line === "") flush();
          else if (line.startsWith(":")) { /* 心跳 */ }
          else if (line.startsWith("event:")) currentEvent = line.slice(6).trim();
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^\s/, ""));
        }
      }
    } catch (e) {
      if (e.name !== "AbortError") onEvent({ type: "error", message: String(e) });
    } finally {
      onClose && onClose();
    }
  })();
  return { abort: () => ctrl.abort() };
}

function Gacha({ onJump }) {
  const [shelf, setShelf]       = React.useState(null);   // /api/gacha/cards
  const [active, setActive]     = React.useState(null);   // /api/gacha/active
  const [genres, setGenres]     = React.useState([]);     // /api/gacha/genres
  const [err, setErr]           = React.useState("");
  const [selectedId, setSelectedId] = React.useState("");
  const [forge, setForge]       = React.useState(null);   // 直播状态（见 startStream）
  const streamRef = React.useRef(null);

  const refresh = React.useCallback(async () => {
    const [s, a] = await Promise.all([API.get("/api/gacha/cards"), API.get("/api/gacha/active")]);
    if (s.error) { setErr(s.error); return; }
    setErr("");
    setShelf(s);
    if (!a.error) setActive(a);
  }, []);

  React.useEffect(() => {
    refresh();
    API.get("/api/gacha/genres").then(g => { if (!g.error) setGenres(g.genres || []); });
    const id = setInterval(refresh, 10000);
    return () => clearInterval(id);
  }, [refresh]);

  /* 发起一次锻造流（draw 或续锻共用），把 SSE 事件折叠进 forge 直播状态。 */
  const startStream = React.useCallback((path, body) => {
    setForge({
      cardId: "", title: "", genreLabel: "", endChapter: 0, chapter: 0, ageRange: "",
      log: [], skills: [], rarity: null, error: "", done: false, closed: false,
    });
    streamRef.current = forgeStream(path, body, (ev) => {
      setForge(f => {
        if (!f) return f;
        const next = { ...f };
        if (ev.type === "card_created" || ev.type === "forge_resume") {
          next.cardId = ev.card_id || "";
          next.title = ev.title || "";
          next.genreLabel = ev.genre_label || "";
          next.endChapter = ev.end_chapter || 0;
          next.chapter = ev.current_chapter || 0;
          next.seed = ev;
        } else if (ev.type === "chapter_start") {
          next.chapter = ev.chapter || next.chapter;
          next.endChapter = ev.end_chapter || next.endChapter;
          next.ageRange = ev.age_range || "";
        } else if (ev.type === "chapter_done") {
          next.chapter = ev.chapter || next.chapter;
          next.log = [...f.log, { chapter: ev.chapter, age_range: ev.age_range, report: ev.report || "" }];
        } else if (ev.type === "skill_installed") {
          next.skills = [...f.skills, ...(ev.skills || [])];
        } else if (ev.type === "rarity") {
          next.rarity = { grade: ev.grade, score: ev.score, comment: ev.comment, title: ev.title };
          if (ev.title) next.title = ev.title;
        } else if (ev.type === "error") {
          next.error = ev.message || "锻造失败";
        } else if (ev.type === "done") {
          next.done = true;
          if (ev.title) next.title = ev.title;
        }
        return next;
      });
      if (ev.type === "done" || ev.type === "error") refresh();
    }, () => {
      setForge(f => (f ? { ...f, closed: true } : f));
      refresh();
    });
  }, [refresh]);

  const draw = React.useCallback((params) => {
    startStream("/api/gacha/draw", params);
  }, [startStream]);

  const continueForge = React.useCallback((cardId) => {
    startStream(`/api/gacha/cards/${encodeURIComponent(cardId)}/forge`, {});
  }, [startStream]);

  const rebirth = React.useCallback(async (card) => {
    const name = card.title || card.card_id;
    const msg = `确定转生为《${name}》（${card.age} 岁）？\n\n` +
      "转生会把所有渠道（web / REPL / 微信）的当前人格切换为这张卡——微信好友看到的口吻也会变。\n" +
      "创始卡永存，随时可在顶部一键回本源。";
    if (!confirm(msg)) return;
    const r = await API.post(`/api/gacha/cards/${encodeURIComponent(card.card_id)}/rebirth`, {});
    if (r.error) { alert("转生失败：" + r.error); return; }
    refresh();
  }, [refresh]);

  const resetRebirth = React.useCallback(async () => {
    if (!confirm("回到本源：所有渠道的当前人格切回创始卡「三十六贱笑·本源」。确定？")) return;
    const r = await API.post("/api/gacha/rebirth/reset", {});
    if (r.error) { alert("回滚失败：" + r.error); return; }
    refresh();
  }, [refresh]);

  const removeCard = React.useCallback(async (card) => {
    const name = card.title || card.card_id;
    if (!confirm(`确定删除《${name}》？整张卡（传记/人格/评级）将被移除，不可撤销。\n已自动安装的外部 skill 不会卸载。`)) return;
    const r = await API.del(`/api/gacha/cards/${encodeURIComponent(card.card_id)}`);
    if (r.error) { alert("删除失败：" + r.error); return; }
    if (selectedId === card.card_id) setSelectedId("");
    refresh();
  }, [refresh, selectedId]);

  const exportCard = React.useCallback(async (cardId) => {
    const d = await API.get(`/api/gacha/cards/${encodeURIComponent(cardId)}`);
    if (d.error) { alert("导出失败：" + d.error); return; }
    API.download(`${cardId}.json`, JSON.stringify(d, null, 2));
  }, []);

  const enabled = !!(shelf && shelf.enabled);
  const cards = (shelf && shelf.cards) || [];
  const anyForging = !!forge && !forge.done && !forge.error && !forge.closed;
  const quotaLeft = shelf
    ? (shelf.daily_draw_limit > 0 ? Math.max(0, shelf.daily_draw_limit - shelf.draws_today) : Infinity)
    : 0;

  return (
    <div data-screen-label="11 抽卡">
      <PageHeader
        title="抽卡 · 人生卡池"
        sub={shelf
          ? `${shelf.count} 张卡 · 今日已抽 ${shelf.draws_today}${shelf.daily_draw_limit > 0 ? `/${shelf.daily_draw_limit}` : ""} · ${enabled ? "已启用" : "未启用"}`
          : "加载中…"}
        actions={<button className="btn btn-secondary" onClick={refresh}><Icon name="refresh" size={13}/>刷新</button>}
      />

      <div className="page-body">
        {err && (
          <div className="card card-padded" style={{ marginBottom: 12, background: "var(--danger-bg)", borderColor: "rgba(212,57,44,0.3)" }}>
            <span className="t-meta" style={{ color: "var(--danger-fg)" }}>读取卡池失败：{err}</span>
          </div>
        )}

        <ActiveBar active={active} onReset={resetRebirth} />

        {!enabled && shelf && (
          <div className="card card-padded" style={{ marginBottom: 16 }}>
            <span className="t-meta" style={{ color: "var(--ink-60)" }}>
              抽卡平台未启用：在 .env 设 <code className="t-mono-sm">SANSHILIU_GACHA_ENABLED=true</code> 后重启 serve。
              卡册只读可看，抽卡/续锻/转生/删卡会被拒（回本源除外）。
            </span>
          </div>
        )}

        <ForgePanel
          genres={genres}
          disabled={!enabled || anyForging || quotaLeft <= 0}
          disabledReason={!enabled ? "平台未启用" : anyForging ? "有卡正在锻造" : quotaLeft <= 0 ? "今日额度已用完" : ""}
          onDraw={draw}
        />

        {forge && <ForgeLive forge={forge} onDismiss={() => setForge(null)} />}

        <div className="t-section" style={{ margin: "20px 0 12px" }}>卡册</div>
        {cards.length === 0 ? (
          <div className="card card-padded t-meta" style={{ textAlign: "center", color: "var(--ink-60)" }}>
            还没有任何卡。{enabled ? "在上方锻造台抽第一张。" : "先启用平台再抽。"}
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 12 }}>
            {cards.map(c => (
              <CardFace
                key={c.card_id}
                card={c}
                isActive={!!(active && active.active && active.active.card_id === c.card_id)}
                selected={selectedId === c.card_id}
                onClick={() => setSelectedId(selectedId === c.card_id ? "" : c.card_id)}
              />
            ))}
          </div>
        )}

        {selectedId && (
          <CardDetail
            key={selectedId}
            cardId={selectedId}
            isActiveCard={!!(active && active.active && active.active.card_id === selectedId)}
            enabled={enabled}
            anyForging={anyForging}
            onRebirth={rebirth}
            onContinue={continueForge}
            onDelete={removeCard}
            onExport={exportCard}
            onClose={() => setSelectedId("")}
          />
        )}
      </div>
    </div>
  );
}

/* 顶部当前真身条：来源（指针/默认创始卡/base core）+ 一键回本源。 */
function ActiveBar({ active, onReset }) {
  if (!active) return null;
  const a = active.active || {};
  const card = active.card;
  const name = card ? (card.title || card.card_id) : "base persona/core";
  const isOrigin = a.card_id === "origin";
  const sourceText = a.source === "base_core"
    ? "未迁移：使用基础人格"
    : `第 ${a.resolved_chapter} 章 · ${card ? card.age + " 岁" : ""}${a.source === "default_origin" ? "（默认）" : ""}`;
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="card-body" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 20 }}>{card ? card.genre_icon : "👤"}</span>
          <div>
            <div className="t-card-title">当前真身：{name}</div>
            <div className="t-meta" style={{ color: "var(--ink-60)", marginTop: 2 }}>
              {sourceText} · 全渠道生效（web / REPL / 微信）
            </div>
          </div>
          {card && card.grade && <RarityChip grade={card.grade} />}
        </div>
        {!isOrigin && a.source === "pointer" && (
          <button className="btn btn-secondary btn-sm" onClick={onReset}>
            <Icon name="refresh" size={12}/>回本源（三十六贱笑）
          </button>
        )}
      </div>
    </div>
  );
}

/* 锻造台：世界类型格 + 触发事件预览 + 创意度 + 自定义补充 → 抽卡。 */
function ForgePanel({ genres, disabled, disabledReason, onDraw }) {
  const [genre, setGenre] = React.useState("random");
  const [creativityMode, setCreativityMode] = React.useState("random"); // random | manual
  const [creativity, setCreativity] = React.useState(1.0);
  const [prompt, setPrompt] = React.useState("");

  const spec = genres.find(g => g.id === genre);

  const submit = () => {
    const params = {};
    if (genre && genre !== "random") params.genre = genre;
    if (creativityMode === "manual") params.creativity = Number(creativity);
    if (prompt.trim()) params.custom_prompt = prompt.trim();
    onDraw(params);
  };

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <CardHeader title="锻造台" sub="抽一张人生卡：随机命运种子 → 同步连锻 11 章（5→60 岁）→ 跑完评级" />
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <div>
          <div className="t-eyebrow" style={{ marginBottom: 6 }}>世界类型</div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <GenreChip active={genre === "random"} icon="🎲" label="随机" onClick={() => setGenre("random")} />
            {genres.map(g => (
              <GenreChip key={g.id} active={genre === g.id} icon={g.icon} label={g.label} onClick={() => setGenre(g.id)} />
            ))}
          </div>
          {spec && (
            <div className="t-meta" style={{ color: "var(--ink-60)", marginTop: 6 }}>
              可能的命运触发：{spec.triggers.join(" / ")}
            </div>
          )}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <div className="t-eyebrow">创意度</div>
          <label className="t-meta" style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
            <input type="radio" checked={creativityMode === "random"} onChange={() => setCreativityMode("random")} />随机
          </label>
          <label className="t-meta" style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
            <input type="radio" checked={creativityMode === "manual"} onChange={() => setCreativityMode("manual")} />指定
          </label>
          {creativityMode === "manual" && (
            <>
              <span className="t-meta" style={{ color: "var(--ink-60)" }}>保守</span>
              <input type="range" min="0" max="2" step="0.1" value={creativity}
                     onChange={e => setCreativity(e.target.value)} style={{ width: 160 }} />
              <span className="t-meta" style={{ color: "var(--ink-60)" }}>狂野</span>
              <span className="t-mono-sm">{Number(creativity).toFixed(1)}</span>
            </>
          )}
        </div>

        <div>
          <div className="t-eyebrow" style={{ marginBottom: 6 }}>自定义补充（可空）</div>
          <textarea className="field" rows={2} value={prompt} maxLength={2000}
                    onChange={e => setPrompt(e.target.value)}
                    placeholder="补充设定，例如：性别女 / 初中在校门口捡到一枚刻着古文的戒指…" />
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <button className="btn btn-primary" disabled={disabled} onClick={submit}>
            <Icon name="spark" size={13} color="#fff" />⚡ 抽卡（锻一生约 10-20 分钟）
          </button>
          {disabled && disabledReason && (
            <span className="t-meta" style={{ color: "var(--ink-60)" }}>{disabledReason}</span>
          )}
        </div>
      </div>
    </div>
  );
}

function GenreChip({ active, icon, label, onClick }) {
  return (
    <span className={`chip ${active ? "chip-info" : ""}`}
          style={{ cursor: "pointer", userSelect: "none" }}
          onClick={onClick}>
      {icon} {label}
    </span>
  );
}

/* 锻造直播：进度条 + 逐章汇报滚动 + 装上的 skill + 评级揭晓。 */
function ForgeLive({ forge, onDismiss }) {
  const pct = forge.endChapter > 0 ? Math.round((forge.chapter / forge.endChapter) * 100) : 0;
  const running = !forge.done && !forge.error && !forge.closed;
  const rs = forge.rarity ? rarityStyle(forge.rarity.grade) : null;
  const logRef = React.useRef(null);
  React.useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [forge.log.length]);

  return (
    <div className="card" style={{ marginBottom: 16, borderColor: running ? "var(--primary)" : undefined }}>
      <CardHeader
        title={running ? `🔥 锻造中 ${forge.cardId}`
          : forge.error ? "锻造中断"
          : forge.done ? `锻造完成 ${forge.title ? `《${forge.title}》` : forge.cardId}`
          : "连接已断开（锻造在服务端继续，稍后刷新卡册）"}
        sub={forge.seed ? `${forge.seed.genre_icon || ""} ${forge.genreLabel} · 出身：${forge.seed.origin || "—"} · 触发：${forge.seed.trigger || "—"} · 创意度 ${forge.seed.creativity}` : ""}
        right={!running ? <button className="btn btn-ghost btn-sm" onClick={onDismiss}>收起</button> : <span className="chip chip-info">第 {forge.chapter}/{forge.endChapter} 章</span>}
      />
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={{ height: 8, borderRadius: 999, background: "var(--hairline)", overflow: "hidden" }}>
          <div style={{ width: `${pct}%`, height: "100%", background: "var(--primary)", transition: "width 400ms cubic-bezier(0.32,0.72,0,1)" }} />
        </div>
        {running && (
          <div className="t-meta" style={{ color: "var(--ink-60)" }}>
            正在锻 {forge.ageRange ? `${forge.ageRange}${String(forge.ageRange).includes("岁") ? "" : " 岁"}` : "…"} 这一章
            （每章约 1-3 分钟）；关掉页面锻造也会继续，回来刷新即可。
          </div>
        )}
        {forge.log.length > 0 && (
          <div ref={logRef} style={{ maxHeight: 220, overflowY: "auto", display: "flex", flexDirection: "column", gap: 8 }}>
            {forge.log.map(l => (
              <div key={l.chapter} className="card" style={{ background: "var(--pearl)", padding: "8px 12px" }}>
                {/* age_range 可能已含"岁"（LLM 自带年代注），别再拼出"岁 岁" */}
                <div className="t-meta-strong" style={{ marginBottom: 2 }}>
                  第 {l.chapter} 章 · {l.age_range}{String(l.age_range).includes("岁") ? "" : " 岁"}
                </div>
                <div className="t-row" style={{ whiteSpace: "pre-wrap" }}>{l.report}</div>
              </div>
            ))}
          </div>
        )}
        {forge.skills.length > 0 && (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            <span className="t-meta" style={{ color: "var(--ink-60)" }}>途中装上：</span>
            {forge.skills.map(s => <span key={s} className="chip chip-mono">{s}</span>)}
          </div>
        )}
        {forge.rarity && (
          <div className="card" style={{ padding: "12px 14px", borderWidth: 2, borderColor: rs.border, background: rs.bg }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
              <span className="t-stat" style={{ color: rs.color }}>{forge.rarity.grade || "—"}</span>
              <span className="t-card-title">《{forge.rarity.title || forge.title || "未命名"}》</span>
              <span className="t-meta">{forge.rarity.score} 分</span>
            </div>
            <div className="t-row" style={{ marginTop: 4 }}>{forge.rarity.comment}</div>
          </div>
        )}
        {forge.error && (
          <div className="t-meta" style={{ color: "var(--danger-fg)" }}>
            {forge.error}（已成立的章保留；可在卡册对这张卡点「续锻」重试）
          </div>
        )}
      </div>
    </div>
  );
}

function RarityChip({ grade }) {
  const rs = rarityStyle(grade);
  return (
    <span className="chip" style={{ borderColor: rs.border, color: rs.color, background: rs.bg, fontWeight: 700 }}>
      {grade}
    </span>
  );
}

/* 卡册单卡卡面：稀有度描边 + genre 图标 + 标题 + 状态/年龄徽标。 */
function CardFace({ card, isActive, selected, onClick }) {
  const rs = rarityStyle(card.grade);
  return (
    <div className="card" onClick={onClick}
         style={{
           cursor: "pointer",
           borderWidth: 2,
           borderColor: selected ? "var(--primary)" : rs.border,
           background: rs.bg,
           padding: "14px 14px 12px",
         }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <span style={{ fontSize: 26 }}>{card.genre_icon}</span>
        {card.grade ? <RarityChip grade={card.grade} /> : <span className="chip" style={{ color: "var(--ink-60)" }}>{STATUS_LABEL[card.status] || card.status}</span>}
      </div>
      <div className="t-card-title" style={{ marginTop: 8 }}>
        {card.title || "（未命名 · 锻满后评级命名）"}
      </div>
      <div className="t-meta" style={{ color: "var(--ink-60)", marginTop: 2 }}>
        {card.genre_label} · {card.age} 岁 · {card.current_chapter}/{card.end_chapter} 章
      </div>
      <div className="t-meta" style={{ color: "var(--ink-48)", marginTop: 6 }}>
        {truncateG(card.origin, 18)}{card.trigger ? ` · ${truncateG(card.trigger, 14)}` : ""}
      </div>
      <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
        {isActive && <span className="chip chip-success">当前真身</span>}
        {card.is_origin && <span className="chip">创始卡</span>}
        {card.status !== "complete" && <span className="chip" style={{ color: "var(--ink-60)" }}>{STATUS_LABEL[card.status]}</span>}
        {card.skill_count > 0 && <span className="chip">{card.skill_count} skill</span>}
      </div>
    </div>
  );
}

/* 卡详情：种子 + 评级 + 操作 + 逐章时间轴（按需拉传记/人格）。 */
function CardDetail({ cardId, isActiveCard, enabled, anyForging, onRebirth, onContinue, onDelete, onExport, onClose }) {
  const [detail, setDetail]   = React.useState(null);
  const [err, setErr]         = React.useState("");
  const [expanded, setExpanded] = React.useState({});
  const [chDetail, setChDetail] = React.useState({});
  const [persona, setPersona]   = React.useState({});

  React.useEffect(() => {
    let alive = true;
    API.get(`/api/gacha/cards/${encodeURIComponent(cardId)}`).then(d => {
      if (!alive) return;
      if (d.error) { setErr(d.error); return; }
      setDetail(d);
    });
    return () => { alive = false; };
  }, [cardId]);

  const toggleChapter = async (n) => {
    const next = !expanded[n];
    setExpanded(e => ({ ...e, [n]: next }));
    if (!next) return;
    if (!chDetail[n]) {
      const d = await API.get(`/api/gacha/cards/${encodeURIComponent(cardId)}/chapters/${n}`);
      if (!d.error) setChDetail(s => ({ ...s, [n]: d }));
    }
    if (!persona[n]) {
      const p = await API.get(`/api/gacha/cards/${encodeURIComponent(cardId)}/persona/${n}`);
      if (!p.error) setPersona(s => ({ ...s, [n]: p.files || [] }));
    }
  };

  if (err) return <div className="card card-padded t-meta" style={{ marginTop: 16, color: "var(--danger-fg)" }}>读取卡详情失败:{err}</div>;
  if (!detail) return <div className="card card-padded t-meta" style={{ marginTop: 16 }}>加载卡详情…</div>;

  const seed = detail.seed || {};
  const rarity = detail.rarity || {};
  const chapters = detail.chapters || [];
  const rs = rarityStyle(rarity.grade);
  const summaryCard = { card_id: detail.card_id, title: detail.title, age: detail.age };
  const canContinue = detail.status !== "complete" && detail.status !== "forging";

  return (
    <div className="card" style={{ marginTop: 16, borderWidth: 2, borderColor: rs.border }}>
      <CardHeader
        eyebrow={`${detail.card_id}${detail.is_origin ? " · 创始卡" : ""}`}
        title={`《${detail.title || "未命名"}》`}
        sub={`${seed.genre_label || ""} · ${detail.age} 岁 · 第 ${detail.current_chapter}/${detail.end_chapter} 章 · ${STATUS_LABEL[detail.status] || detail.status}`}
        right={<button className="btn btn-ghost btn-sm" onClick={onClose}>关闭</button>}
      />
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {/* 评级 */}
        {rarity.grade && (
          <div className="card" style={{ padding: "12px 14px", borderWidth: 2, borderColor: rs.border, background: rs.bg }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
              <span className="t-stat" style={{ color: rs.color }}>{rarity.grade}</span>
              <span className="t-meta">{rarity.score} 分</span>
            </div>
            <div className="t-row" style={{ marginTop: 4 }}>{rarity.comment}</div>
          </div>
        )}

        {/* 命运种子 */}
        <div>
          <div className="t-eyebrow" style={{ marginBottom: 8 }}>命运种子</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <KV k="出身" v={seed.origin || "—"} />
            <KV k="天赋" v={(seed.talents || []).join("、") || "—"} />
            <KV k="触发事件" v={seed.trigger || "—"} />
            <KV k="创意度" v={String(seed.creativity != null ? seed.creativity : "—")} mono />
            {seed.custom_prompt && <KV k="主人补充" v={seed.custom_prompt} />}
          </div>
        </div>

        {/* 操作 */}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {!isActiveCard && detail.current_chapter > 0 && (
            <button className="btn btn-primary btn-sm" disabled={!enabled || detail.status === "forging"}
                    onClick={() => onRebirth({ ...summaryCard })}>
              转生为这张卡
            </button>
          )}
          {isActiveCard && <span className="chip chip-success" style={{ alignSelf: "center" }}>当前真身</span>}
          {canContinue && (
            <button className="btn btn-secondary btn-sm" disabled={!enabled || anyForging}
                    onClick={() => onContinue(detail.card_id)}>
              续锻到 {detail.end_age} 岁
            </button>
          )}
          <button className="btn btn-secondary btn-sm" onClick={() => onExport(detail.card_id)}>导出 JSON</button>
          {!detail.is_origin && (
            <button className="btn btn-ghost btn-sm" disabled={!enabled || detail.status === "forging"}
                    onClick={() => onDelete({ ...summaryCard })}>
              删除
            </button>
          )}
        </div>

        {/* 人生时间轴 */}
        <div>
          <div className="t-eyebrow" style={{ marginBottom: 8 }}>人生时间轴（{chapters.length} 章）</div>
          {chapters.length === 0 ? (
            <span className="t-meta" style={{ color: "var(--ink-60)" }}>还没有任何章。</span>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {chapters.map((ch, i) => {
                const n = i + 1;
                return (
                  <GachaChapter
                    key={n}
                    n={n}
                    ch={ch}
                    activePersona={detail.active_persona_chapter === n}
                    open={!!expanded[n]}
                    detail={chDetail[n]}
                    persona={persona[n]}
                    onToggle={() => toggleChapter(n)}
                  />
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function GachaChapter({ n, ch, activePersona, open, detail, persona, onToggle }) {
  const skills = ch.installed_skills || [];
  return (
    <div className="card">
      <div className="card-header" style={{ cursor: "pointer" }} onClick={onToggle}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{
            width: 28, height: 28, borderRadius: 8,
            background: "var(--ink)", color: "#fff",
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 600,
          }}>{n}</span>
          <div>
            <div className="t-card-title">第 {n} 章 · {ch.age_range}</div>
            <div className="t-meta" style={{ color: "var(--ink-60)", marginTop: 2 }}>
              {truncateG(ch.report || ch.summary, 64)}
            </div>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {activePersona && <span className="chip chip-info">当前人格章</span>}
          {skills.length > 0 && <span className="chip">{skills.length} skill</span>}
          <Icon name={open ? "chevron-u" : "chevron-d"} size={14} />
        </div>
      </div>
      {open && (
        <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div>
            <div className="t-eyebrow" style={{ marginBottom: 6 }}>锻造汇报</div>
            <div className="t-body" style={{ whiteSpace: "pre-wrap" }}>{ch.report || "（无汇报）"}</div>
          </div>
          <div>
            <div className="t-eyebrow" style={{ marginBottom: 6 }}>传记全文</div>
            <div className="t-body" style={{ whiteSpace: "pre-wrap", color: "var(--ink-80)" }}>
              {detail ? (detail.biography || detail.summary || "（传记为空）") : "加载中…"}
            </div>
          </div>
          <div>
            <div className="t-eyebrow" style={{ marginBottom: 6 }}>本章习得 skills</div>
            {skills.length === 0 ? (
              <span className="t-meta" style={{ color: "var(--ink-60)" }}>这一章没装到真实 skill（不报错）</span>
            ) : (
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {skills.map(s => <span key={s} className="chip chip-mono">{s}</span>)}
              </div>
            )}
          </div>
          <div>
            <div className="t-eyebrow" style={{ marginBottom: 6 }}>人格快照 · persona/chapter-{n}/</div>
            {persona === undefined ? (
              <span className="t-meta">加载中…</span>
            ) : persona.length === 0 ? (
              <span className="t-meta" style={{ color: "var(--ink-60)" }}>本章没有覆盖人格段落（承接前章）</span>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {persona.map(f => (
                  <div key={f.name}>
                    <div className="t-meta-strong" style={{ marginBottom: 4 }}>
                      {f.section} <span className="t-mono-sm" style={{ color: "var(--ink-48)" }}>({f.name})</span>
                    </div>
                    <div className="card" style={{ background: "var(--pearl)", padding: "10px 12px" }}>
                      <div className="t-row" style={{ whiteSpace: "pre-wrap" }}>{f.body}</div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function truncateG(s, n) {
  if (!s) return "";
  const str = String(s);
  return str.length > n ? str.slice(0, n) + "…" : str;
}

Object.assign(window, { Gacha });
