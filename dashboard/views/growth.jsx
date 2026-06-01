/* Growth — 成长模块（PR4 / prd #1/#3/#4）。
 * 数字分身每天做一次"成长梦"，从 5 岁起每章跨 5 年长到 30 岁定格。本视图只管"看结果 + 设置说明"：
 *   ① 设置：growth_enabled 状态 + 指向心跳模块做调度/手动推进（触发与心跳合并，#3）。
 *   ② 时间线：start_age→end_age、当前章/岁、进度。
 *   ③ 每章卡片：传记/叙事、汇报、习得 skills、人格快照（按需 fetch /api/growth/persona/{n}）。
 *   ④ 做梦/成长历史列表。
 * 数据全部来自 /api/growth*；调度动作走现成 /api/heartbeat/growth/*（在心跳模块操作）。
 * 无构建步：浏览器内 Babel-React，挂全局函数，约定同其它 views。
 */

function Growth({ onJump }) {
  const [data, setData]         = React.useState(null);   // /api/growth 总览
  const [err,  setErr]          = React.useState("");
  const [expanded, setExpanded] = React.useState({});     // chapter_no → bool
  const [detail, setDetail]     = React.useState({});     // chapter_no → 章详情（含 biography）
  const [persona, setPersona]   = React.useState({});     // chapter_no → 人格快照 files

  const refresh = React.useCallback(async () => {
    const r = await API.get("/api/growth");
    if (r.error) { setErr(r.error); return; }
    setErr("");
    setData(r);
  }, []);

  React.useEffect(() => {
    refresh();
    const id = setInterval(refresh, 10000);
    return () => clearInterval(id);
  }, [refresh]);

  // 展开某章：拉详情（传记正文）+ 人格快照（各只拉一次）
  const toggleChapter = async (n) => {
    const next = !expanded[n];
    setExpanded(e => ({ ...e, [n]: next }));
    if (!next) return;
    if (!detail[n]) {
      const d = await API.get(`/api/growth/chapters/${n}`);
      if (!d.error) setDetail(s => ({ ...s, [n]: d }));
    }
    if (!persona[n]) {
      const p = await API.get(`/api/growth/persona/${n}`);
      if (!p.error) setPersona(s => ({ ...s, [n]: p.files || [] }));
    }
  };

  const chapters = (data && data.chapters) || [];
  const enabled  = !!(data && data.enabled);
  const frozen   = !!(data && data.frozen);

  return (
    <div data-screen-label="11 成长">
      <PageHeader
        title="成长"
        sub={data
          ? `${data.current_chapter}/${data.end_chapter} 章 · 当前 ${data.age} 岁 · ${enabled ? "已启用" : "未启用"}${frozen ? " · 已定格" : ""}`
          : "加载中…"}
        actions={
          <>
            <button className="btn btn-secondary" onClick={() => onJump("heartbeat")}>
              <Icon name="spark" size={13}/>去心跳模块调度
            </button>
            <button className="btn btn-secondary" onClick={refresh}><Icon name="refresh" size={13}/>刷新</button>
          </>
        }
      />

      <div className="page-body">
        {err && (
          <div className="card card-padded" style={{ marginBottom: 12, background: "var(--danger-bg)", borderColor: "rgba(212,57,44,0.3)" }}>
            <span className="t-meta" style={{ color: "var(--danger-fg)" }}>读取成长状态失败：{err}</span>
          </div>
        )}

        {data && (
          <>
            <GrowthSettings data={data} onJump={onJump} />
            <Timeline data={data} />

            <div className="t-section" style={{ margin: "20px 0 12px" }}>成长历程</div>
            {chapters.length === 0 ? (
              <div className="card card-padded t-meta" style={{ textAlign: "center", color: "var(--ink-60)" }}>
                还没有任何成长章。{enabled
                  ? "可去心跳模块手动推进第 1 章，或等定时触发。"
                  : "先启用成长心跳任务（在心跳模块），再手动推进或等定时触发。"}
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                {chapters.map(ch => (
                  <ChapterCard
                    key={ch.chapter_no}
                    ch={ch}
                    active={data.active_persona_chapter === ch.chapter_no}
                    open={!!expanded[ch.chapter_no]}
                    detail={detail[ch.chapter_no]}
                    persona={persona[ch.chapter_no]}
                    onToggle={() => toggleChapter(ch.chapter_no)}
                  />
                ))}
              </div>
            )}

            <DreamHistory chapters={chapters} />
          </>
        )}
      </div>
    </div>
  );
}

/* 设置卡：成长开关状态（只读展示）+ 指向心跳模块做调度/启停/手动推进。 */
function GrowthSettings({ data, onJump }) {
  const enabled = !!data.enabled;
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <CardHeader title="设置" sub="触发与调度已合并进心跳模块（成长是一个 HeartbeatTask）" />
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <div>
            <div className="t-row-strong">成长引擎</div>
            <div className="t-meta" style={{ color: "var(--ink-60)", marginTop: 2 }}>
              .env <code className="t-mono-sm">SANSHILIU_GROWTH_ENABLED</code> 是首次启动 seed；
              运行时开关在心跳模块即时切换并落盘。同时也是外部 skill 自动安装的总开关。
            </div>
          </div>
          <span className={`chip chip-dot ${enabled ? "chip-success" : ""}`} style={enabled ? {} : { color: "var(--ink-60)" }}>
            {enabled ? "已启用" : "未启用"}
          </span>
        </div>

        <div style={{ borderTop: "1px solid var(--hairline)", paddingTop: 14, display: "flex", flexDirection: "column", gap: 8 }}>
          <KV k="调度 / 启停 / 手动推进" v="心跳模块 → growth 任务" />
          <KV k="规则" v="每天一章 · 满 30 岁永久定格" />
          <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
            <button className="btn btn-primary btn-sm" onClick={() => onJump("heartbeat")}>
              <Icon name="arrow-r" size={11} color="#fff" />前往心跳模块
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* 时间线卡：start_age→end_age 进度条 + 当前章/岁 + 各章年龄段刻度。 */
function Timeline({ data }) {
  const t = data.timeline || { start_age: data.start_age, end_age: data.end_age, current_age: data.age };
  const span = Math.max(1, t.end_age - t.start_age);
  const pct  = Math.min(100, Math.max(0, ((t.current_age - t.start_age) / span) * 100));
  const total = data.end_chapter || 0;

  return (
    <div className="card">
      <CardHeader title="时间线" sub={`${t.start_age} 岁 → ${t.end_age} 岁`} right={
        <span className="chip chip-info">{data.current_chapter}/{total} 章</span>
      } />
      <div className="card-body">
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <span className="t-stat">{t.current_age}</span>
          <span className="t-meta">岁 · 当前激活人格：第 {data.active_persona_chapter} 章</span>
        </div>
        {/* 进度条 */}
        <div style={{ marginTop: 14, height: 8, borderRadius: 999, background: "var(--hairline)", overflow: "hidden" }}>
          <div style={{ width: `${pct}%`, height: "100%", background: "var(--primary)", transition: "width 300ms cubic-bezier(0.32,0.72,0,1)" }} />
        </div>
        {/* 章刻度 */}
        <div style={{ display: "flex", gap: 6, marginTop: 12, flexWrap: "wrap" }}>
          {Array.from({ length: total }, (_, i) => i + 1).map(no => {
            const done = no <= data.current_chapter;
            const lo = t.start_age + (no - 1) * (data.years_per_chapter || 5);
            const hi = lo + (data.years_per_chapter || 5);
            return (
              <span key={no} className={`chip ${done ? "chip-success" : ""}`}
                    style={done ? {} : { color: "var(--ink-60)" }}>
                第{no}章 {lo}-{hi}
              </span>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/* 单章卡：折叠头（年龄段 + 汇报摘要 + skills 计数）；展开后显示传记正文 + 汇报全文 + 习得 skills + 人格快照。 */
function ChapterCard({ ch, active, open, detail, persona, onToggle }) {
  const skills = ch.installed_skills || [];
  return (
    <div className="card">
      <div className="card-header" style={{ cursor: "pointer" }} onClick={onToggle}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{
            width: 30, height: 30, borderRadius: 8,
            background: "var(--ink)", color: "#fff",
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 600,
          }}>{ch.chapter_no}</span>
          <div>
            <div className="t-card-title">第 {ch.chapter_no} 章 · {ch.age_range} 岁</div>
            <div className="t-meta" style={{ color: "var(--ink-60)", marginTop: 2 }}>
              {ch.report ? truncate(ch.report, 60) : truncate(ch.summary, 60)}
            </div>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {active && <span className="chip chip-info">当前人格</span>}
          {skills.length > 0 && <span className="chip">{skills.length} skill</span>}
          <Icon name={open ? "chevron-u" : "chevron-d"} size={14} />
        </div>
      </div>

      {open && (
        <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {/* 汇报全文 */}
          <Section title="当天汇报">
            <div className="t-body" style={{ whiteSpace: "pre-wrap" }}>{ch.report || "（无汇报）"}</div>
          </Section>

          {/* 传记 / 叙事正文（读 memdir） */}
          <Section title="成长传记 / 叙事">
            <div className="t-body" style={{ whiteSpace: "pre-wrap", color: "var(--ink-80)" }}>
              {detail ? (detail.biography || detail.summary || "（传记读取中或为空）") : "加载中…"}
            </div>
          </Section>

          {/* 习得 skills */}
          <Section title="本章习得 skills">
            {skills.length === 0 ? (
              <span className="t-meta" style={{ color: "var(--ink-60)" }}>这一章没找到可装的真实 skill（不报错）</span>
            ) : (
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {skills.map(s => <span key={s} className="chip chip-mono">{s}</span>)}
              </div>
            )}
          </Section>

          {/* 人格快照 */}
          <Section title={`人格快照 · data/growth/persona/chapter-${ch.chapter_no}/`}>
            {persona === undefined ? (
              <span className="t-meta">加载中…</span>
            ) : persona.length === 0 ? (
              <span className="t-meta" style={{ color: "var(--ink-60)" }}>这一章没有覆盖人格段落（承接前章）</span>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {persona.map(f => (
                  <div key={f.name}>
                    <div className="t-meta-strong" style={{ marginBottom: 4 }}>{f.section} <span className="t-mono-sm" style={{ color: "var(--ink-48)" }}>({f.name})</span></div>
                    <div className="card" style={{ background: "var(--pearl)", padding: "10px 12px" }}>
                      <div className="t-row" style={{ whiteSpace: "pre-wrap" }}>{f.body}</div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Section>
        </div>
      )}
    </div>
  );
}

/* 做梦 / 成长历史：按章倒序列出年龄段 + 创建时间 + skills 数，给一个时间脉络。 */
function DreamHistory({ chapters }) {
  if (!chapters || chapters.length === 0) return null;
  const rows = [...chapters].reverse();
  return (
    <>
      <div className="t-section" style={{ margin: "24px 0 12px" }}>做梦历史</div>
      <div className="card">
        <table className="tbl">
          <thead>
            <tr>
              <th style={{ width: 60 }}>章</th>
              <th style={{ width: 110 }}>年龄段</th>
              <th>汇报摘要</th>
              <th style={{ width: 100 }}>习得</th>
              <th style={{ width: 160 }}>时间</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(ch => (
              <tr key={ch.chapter_no}>
                <td className="cell-strong">第{ch.chapter_no}章</td>
                <td className="t-mono-sm">{ch.age_range}</td>
                <td className="t-row">{truncate(ch.report || ch.summary, 80)}</td>
                <td className="t-meta">{(ch.installed_skills || []).length} 个</td>
                <td className="t-meta">{ch.created_at ? API.relTime(ch.created_at * 1000) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Section({ title, children }) {
  return (
    <div>
      <div className="t-eyebrow" style={{ marginBottom: 6 }}>{title}</div>
      {children}
    </div>
  );
}

function truncate(s, n) {
  if (!s) return "";
  const str = String(s);
  return str.length > n ? str.slice(0, n) + "…" : str;
}

Object.assign(window, { Growth });
