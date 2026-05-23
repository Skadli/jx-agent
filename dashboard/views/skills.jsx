/* Skills — admin table + optional grid. No marketing hero. */

const SKILLS = [
  {
    id: "video-editor",
    name: "video-editor",
    title: "视频脚本拆解",
    desc: "把任何主题用「钩子 → 反转 → 拆解 → 收尾」四拍重写。",
    triggers: ["脚本", "四拍", "改写", "翻车", "标题"],
    match: "keyword",
    state: "active",
    channels: ["repl", "web", "wechat"],
    hits24: 7,
    hits7d: 41,
    files: 4,
    chars: 5108,
    last: "14 分钟前",
  },
  {
    id: "wechat-style",
    name: "wechat-style",
    title: "微信通道短回复",
    desc: "把任意回复压成微信平均一句 12 字；去书面语，留语气词。",
    triggers: ["微信", "短一点", "别太长"],
    match: "keyword",
    state: "channel-only",
    channels: ["wechat"],
    hits24: 0,
    hits7d: 12,
    files: 2,
    chars: 1830,
    last: "2 天前",
  },
  {
    id: "example-skill",
    name: "example-skill",
    title: "示例 skill",
    desc: "演示 SKILL.md 协议结构。建新 skill 直接 fork 这个目录。",
    triggers: ["示例", "example"],
    match: "keyword",
    state: "active",
    channels: ["repl", "web", "wechat"],
    hits24: 0,
    hits7d: 0,
    files: 3,
    chars: 612,
    last: "1 周前",
  },
];

function Skills({ onJump }) {
  const [view, setView] = React.useState("table");
  const [activeId, setActiveId] = React.useState("video-editor");
  const active = SKILLS.find(s => s.id === activeId);

  return (
    <div data-screen-label="05 技能">
      <PageHeader
        title="技能"
        sub="3 个已注册 · 2 加载中 · 匹配方式: 关键词 · 协议: SKILL.md"
        actions={
          <>
            <Segmented value={view} onChange={setView} options={[
              { id: "table", label: "表格" },
              { id: "grid",  label: "卡片" },
            ]} />
            <button className="btn btn-secondary"><Icon name="external" size={13}/>从 ~/.claude 导入</button>
            <button className="btn btn-primary"><Icon name="plus" size={13} color="#fff"/>新建技能</button>
          </>
        }
      />

      <div className="page-body">
        {/* Stat row */}
        <div className="grid-4">
          <StatCard label="已注册" value="3" sub="2 加载 · 1 仅通道" />
          <StatCard label="24h 命中" value="7" sub="video-editor · 7" trend={{kind:"up", value:"+2"}} />
          <StatCard label="7d 命中" value="53" sub="video-editor · 41 · wechat · 12" trend={{kind:"up", value:"+8"}} />
          <StatCard label="平均匹配延迟" value="3.2" unit="ms" sub="关键词匹配 · O(n) 扫描" />
        </div>

        {/* Skills surface */}
        {view === "table" ? (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 360px", gap: 16, marginTop: 16 }}>
            <div className="card">
              <CardHeader
                title="已注册 skill"
                sub="按 24h 命中排序"
                right={
                  <div className="search-wrap" style={{ width: 200 }}>
                    <span className="search-icon"><Icon name="search" size={13} color="var(--ink-48)"/></span>
                    <input className="search" placeholder="搜索"/>
                  </div>
                }
              />
              <table className="tbl">
                <thead>
                  <tr>
                    <th style={{ width: 36 }}></th>
                    <th>Skill</th>
                    <th style={{ width: 240 }}>触发词</th>
                    <th style={{ width: 110 }}>通道</th>
                    <th style={{ width: 80, textAlign: "right" }}>24h</th>
                    <th style={{ width: 80, textAlign: "right" }}>7d</th>
                    <th style={{ width: 36 }}></th>
                  </tr>
                </thead>
                <tbody>
                  {SKILLS.map(s => (
                    <tr key={s.id} onClick={() => setActiveId(s.id)} style={{ cursor: "pointer", background: activeId === s.id ? "var(--primary-soft)" : "transparent" }}>
                      <td><span className={`dot ${s.state === "active" ? "dot-up" : "dot-off"}`} /></td>
                      <td>
                        <div className="t-mono-strong" style={{ color: activeId === s.id ? "var(--primary)" : "var(--ink)" }}>{s.name}</div>
                        <div className="t-meta" style={{ marginTop: 3 }}>{s.title}</div>
                      </td>
                      <td>
                        <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                          {s.triggers.slice(0, 4).map(t => <span key={t} className="chip" style={{ fontSize: 10.5 }}>{t}</span>)}
                          {s.triggers.length > 4 && <span className="t-meta">+{s.triggers.length - 4}</span>}
                        </div>
                      </td>
                      <td>
                        <div style={{ display: "flex", gap: 4 }}>
                          {s.channels.includes("repl")   && <span className="chip" style={{ fontSize: 10 }}>REPL</span>}
                          {s.channels.includes("web")    && <span className="chip" style={{ fontSize: 10 }}>Web</span>}
                          {s.channels.includes("wechat") && <span className="chip" style={{ fontSize: 10 }}>微信</span>}
                        </div>
                      </td>
                      <td className="col-num" style={{ color: s.hits24 ? "var(--primary)" : "var(--ink-60)" }}>{s.hits24}</td>
                      <td className="col-num">{s.hits7d}</td>
                      <td><button className="btn-icon"><Icon name="chevron-r" size={14}/></button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Detail panel */}
            <SkillDetail s={active} />
          </div>
        ) : (
          <div className="grid-3" style={{ marginTop: 16 }}>
            {SKILLS.map(s => <SkillCard key={s.id} s={s} onClick={() => { setActiveId(s.id); setView("table"); }} />)}
          </div>
        )}

        {/* Recent hits */}
        <div style={{ marginTop: 16 }}>
          <RecentHits />
        </div>
      </div>
    </div>
  );
}

function SkillDetail({ s }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="card">
        <CardHeader
          title={s.name}
          sub={s.title}
          right={<span className={`chip ${s.state === "active" ? "chip-success" : ""}`}>
            {s.state === "active" ? "已加载" : "仅微信"}
          </span>}
        />
        <div className="card-body">
          <p className="t-body" style={{ margin: 0, marginBottom: 16 }}>{s.desc}</p>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <KV k="目录"      v={`skills/${s.name}/`} />
            <KV k="文件数"    v={`${s.files} 份`} />
            <KV k="字数"      v={`${s.chars.toLocaleString()} 字`} />
            <KV k="匹配方式"  v={s.match} />
            <KV k="最近命中"  v={s.last} accent={s.hits24 ? "var(--primary)" : undefined} />
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
            <button className="btn btn-secondary btn-sm grow"><Icon name="doc" size={13}/>SKILL.md</button>
            <button className="btn btn-secondary btn-sm grow"><Icon name="edit" size={13}/>编辑</button>
          </div>
        </div>
      </div>

      <div className="card">
        <CardHeader title="SKILL.md 预览" />
        <pre style={{
          margin: 0,
          padding: "16px 20px",
          fontFamily: "var(--font-mono)",
          fontSize: 11.5,
          lineHeight: 1.7,
          color: "var(--ink-80)",
          whiteSpace: "pre-wrap",
          background: "var(--pearl)",
          borderTop: "1px solid var(--hairline)",
        }}>
{`---
name: ${s.name}
description: |
  ${s.desc}
triggers:
  match: ${s.match}
  any_of: [${s.triggers.map(t => `"${t}"`).join(", ")}]
channels: [${s.channels.join(", ")}]
---

## 用法

读取该 skill 的剩余部分作为
extra-system 注入。`}
        </pre>
      </div>
    </div>
  );
}

function SkillCard({ s, onClick }) {
  return (
    <div className="card" onClick={onClick} style={{ cursor: "pointer" }}>
      <div className="card-padded" style={{ paddingBottom: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <div className="t-mono-strong">{s.name}</div>
            <div className="t-card-title" style={{ marginTop: 4 }}>{s.title}</div>
          </div>
          <span className={`chip ${s.state === "active" ? "chip-success chip-dot" : ""}`}
                style={s.state !== "active" ? { color: "var(--ink-60)" } : {}}>
            {s.state === "active" ? "已加载" : "仅微信"}
          </span>
        </div>
        <p className="t-body" style={{ margin: "12px 0 0", minHeight: 60 }}>{s.desc}</p>
      </div>

      <div style={{ padding: "12px 20px", borderTop: "1px solid var(--hairline)", background: "var(--pearl)" }}>
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 12 }}>
          {s.triggers.map(t => <span key={t} className="chip" style={{ fontSize: 10.5 }}>{t}</span>)}
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span className="t-meta">{s.files} 文件 · {s.chars.toLocaleString()} 字</span>
          <span className="t-mono-sm" style={{ color: s.hits24 ? "var(--primary)" : "var(--ink-60)" }}>{s.hits24} 命中 / 24h</span>
        </div>
      </div>
    </div>
  );
}

const HITS = [
  { t: "14 分钟前", skill: "video-editor", session: "repl-8f2a", trigger: "脚本",     msg: "情侣吵架的视频但怕翻车" },
  { t: "3 小时前",  skill: "video-editor", session: "repl-71d0", trigger: "改写",     msg: "把「我做了 X」改成离谱任务" },
  { t: "5 小时前",  skill: "video-editor", session: "web-2c91",  trigger: "标题",     msg: "标题怎么起" },
  { t: "昨天",      skill: "video-editor", session: "web-44ce",  trigger: "翻车",     msg: "这个梗会不会翻车" },
  { t: "2 天前",    skill: "wechat-style", session: "wechat-a3", trigger: "短一点",   msg: "回复短一点" },
];

function RecentHits() {
  return (
    <div className="card">
      <CardHeader title="最近命中" sub="哪些 skill 在哪些会话被触发了" />
      <table className="tbl">
        <thead>
          <tr>
            <th style={{ width: 110 }}>时间</th>
            <th style={{ width: 160 }}>Skill</th>
            <th style={{ width: 130 }}>会话</th>
            <th style={{ width: 110 }}>触发词</th>
            <th>触发文本</th>
          </tr>
        </thead>
        <tbody>
          {HITS.map((h, i) => (
            <tr key={i}>
              <td className="t-mono-sm" style={{ color: "var(--ink-60)" }}>{h.t}</td>
              <td><span className="t-mono">{h.skill}</span></td>
              <td className="t-mono-sm" style={{ color: "var(--ink-60)" }}>{h.session}</td>
              <td><span className="chip chip-info">{h.trigger}</span></td>
              <td className="t-row" style={{ color: "var(--ink-80)" }}>{h.msg}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

Object.assign(window, { Skills });
