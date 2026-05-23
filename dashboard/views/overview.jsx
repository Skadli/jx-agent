/* Overview — admin dashboard.
 * Page header + 4 stat cards + activity panels + health/budget/layers + recent tool calls.
 */

function Overview({ onJump }) {
  const [range, setRange] = React.useState("24h");

  return (
    <div data-screen-label="01 总览">
      <PageHeader
        title="总览"
        sub={`三十六贱笑 · v1.0.0 · 上次心跳 8 秒前`}
        actions={
        <>
            <Segmented value={range} onChange={setRange} options={[
          { id: "1h", label: "1 小时" },
          { id: "24h", label: "24 小时" },
          { id: "7d", label: "7 天" },
          { id: "30d", label: "30 天" }]
          } />
            <button className="btn btn-secondary"><Icon name="refresh" size={13} />刷新</button>
            <button className="btn btn-primary" onClick={() => onJump("chat")}><Icon name="terminal" size={13} color="#fff" />打开 REPL</button>
          </>
        } />
      

      <div className="page-body">
        {/* ===== 4 KPI cards ===== */}
        <div className="grid-4">
          <StatCard label="会话总数" value="42" sub="REPL · 24  Web · 18  微信 · 0" trend={{ kind: "up", value: "+12%" }} />
          <StatCard label="累计 tokens" value="218,402" sub="输入 142k · 输出 76k" trend={{ kind: "up", value: "+8%" }} />
          <StatCard label="累计成本" value="0.4271" unit="￥" sub="近 14 天 · DeepSeek 折算" trend={{ kind: "up", value: "+￥0.06" }} />
          <StatCard label="平均首字延迟" value="1.21" unit="s" sub="P50 · 流式" trend={{ kind: "down", value: "-0.08s" }} />
        </div>

        {/* ===== Activity + identity + health ===== */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 360px", gap: 16, marginTop: 16, alignItems: "start" }}>
          <SessionsCard onJump={onJump} />
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <IdentityCard onJump={onJump} />
            <HealthCard />
          </div>
        </div>

        {/* ===== Recent tool calls ===== */}
        <div style={{ marginTop: 16 }}>
          <ToolCallsCard onJump={onJump} />
        </div>

        {/* ===== Skills + Memory previews ===== */}
        <div className="grid-2" style={{ marginTop: 16 }}>
          <SkillsPreview onJump={onJump} />
          <MemoryPreview onJump={onJump} />
        </div>
      </div>
    </div>);

}

/* ===================== CARDS ===================== */

const SESSIONS = [
{ id: "repl-8f2a", ch: "REPL", status: "active", tokens: "4,210", cost: "0.0042", t: "2 分钟前", last: "标题怎么起" },
{ id: "web-2c91", ch: "Web", status: "idle", tokens: "18,902", cost: "0.0187", t: "14 分钟前", last: "情侣吵架的视频但怕翻车" },
{ id: "wechat-a3", ch: "微信", status: "closed", tokens: "612", cost: "0.0006", t: "1 小时前", last: "在吗" },
{ id: "repl-71d0", ch: "REPL", status: "closed", tokens: "11,408", cost: "0.0114", t: "3 小时前", last: "把「我做了 X」改成离谱任务" },
{ id: "web-44ce", ch: "Web", status: "closed", tokens: "2,841", cost: "0.0028", t: "昨天", last: "热梗总结怎么排梗" },
{ id: "web-1aa9", ch: "Web", status: "closed", tokens: "5,612", cost: "0.0056", t: "昨天", last: "情景剧脚本改一下" }];


function SessionsCard({ onJump }) {
  return (
    <div className="card">
      <CardHeader
        title="最近会话"
        sub="近 24 小时 · 6 / 42 条"
        right={
        <>
            <button className="btn btn-ghost btn-sm">导出</button>
            <button className="btn btn-secondary btn-sm" onClick={() => onJump("chat")}>查看全部</button>
          </>
        } />
      
      <table className="tbl">
        <thead>
          <tr>
            <th style={{ width: 130 }}>会话 ID</th>
            <th style={{ width: 80 }}>通道</th>
            <th>最后一句</th>
            <th style={{ width: 92, textAlign: "right" }}>tokens</th>
            <th style={{ width: 80, textAlign: "right" }}>成本</th>
            <th style={{ width: 90, textAlign: "right" }}>时间</th>
          </tr>
        </thead>
        <tbody>
          {SESSIONS.map((s) =>
          <tr key={s.id} onClick={() => onJump("chat")} style={{ cursor: "pointer" }}>
              <td><span className="t-mono" style={{ color: "var(--ink)" }}>{s.id}</span></td>
              <td>
                <span className={`chip ${s.ch === "微信" ? "" : s.ch === "Web" ? "chip-info" : ""}`} style={s.ch === "微信" ? { background: "rgba(193,60,123,0.10)", color: "#9c2f5f" } : {}}>
                  {s.ch}
                </span>
              </td>
              <td className="cell-strong" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 360, color: s.status === "active" ? "var(--ink)" : "var(--ink-80)" }}>
                {s.status === "active" && <span className="dot dot-up" style={{ marginRight: 8 }} />}
                {s.last}
              </td>
              <td className="col-num">{s.tokens}</td>
              <td className="col-num">￥{s.cost}</td>
              <td className="col-num" style={{ color: "var(--ink-60)", fontFamily: "var(--font-text)" }}>{s.t}</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>);

}

function IdentityCard({ onJump }) {
  return (
    <div className="card">
      <CardHeader
        title="实例"
        right={<span className="chip chip-success chip-dot">运行中</span>} />
      
      <div className="card-body">
        <pre className="shadow-product" style={{
          margin: 0,
          background: "var(--tile-1)",
          color: "var(--on-dark)",
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          lineHeight: 1.55,
          padding: "16px 18px",
          borderRadius: 10,
          whiteSpace: "pre",
          overflow: "auto"
        }}>
{`╔══════════════════════════════════╗
║  三十六贱笑 v1.0.0               ║
╠══════════════════════════════════╣
║  Model    gpt-4o-mini            ║
║  Base     api.deepseek.com/v1    ║
║  Persona  28,178 字 / 5 份       ║
║  Skills   3 个 / 2 加载          ║
║  Memory   4,210 字 / 12 条       ║
║  Channels repl, web              ║
╚══════════════════════════════════╝`}
        </pre>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 16 }}>
          <KV k="启动" v="今天 09:12:04" />
          <KV k="运行时长" v="6 小时 24 分" />
          <KV k="PID / 端口" v="48211 / 8080" />
          <KV k="工作目录" v="~/.sanshiliu" />
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
          <button className="btn btn-secondary btn-sm grow" onClick={() => onJump("chat")}><Icon name="chat" size={13}/>打开会话</button>
          <button className="btn btn-secondary btn-sm grow"><Icon name="refresh" size={13}/>重启实例</button>
        </div>
      </div>
    </div>);

}

function HealthCard() {
  const probes = [
  { label: "/healthz", status: "up", value: "200 · 8ms" },
  { label: "/metrics", status: "up", value: "200 · 14ms" },
  { label: "LLM", status: "up", value: "312ms" },
  { label: "SQLite", status: "up", value: "1.2 MB" },
  { label: "持久化磁盘", status: "warn", value: "82%" },
  { label: "iLink 微信", status: "off", value: "未开启" }];

  return (
    <div className="card">
      <CardHeader title="组件健康" sub="探针 · 每 30s 一次" right={<span className="t-mono-sm" style={{ color: "var(--ink-60)" }}>更新于 8s 前</span>} />
      <div className="card-body" style={{ paddingTop: 6 }}>
        {probes.map((p) => <StatusRow key={p.label} {...p} />)}
      </div>
    </div>);

}

const TOOL_CALLS = [
{ t: "2m", tool: "web_search", arg: "情侣 吵架 视频 标题", ms: 412, status: "ok", session: "repl-8f2a" },
{ t: "3m", tool: "file_read", arg: "./persona/style.md", ms: 8, status: "ok", session: "repl-8f2a" },
{ t: "5m", tool: "file_read", arg: "./persona/examples.md", ms: 11, status: "ok", session: "repl-8f2a" },
{ t: "14m", tool: "web_fetch", arg: "https://b23.tv/...", ms: 624, status: "ok", session: "web-2c91" },
{ t: "18m", tool: "bash_exec", arg: "git status", ms: 92, status: "ok", session: "repl-71d0" },
{ t: "32m", tool: "file_write", arg: "./scripts/v3.txt", ms: 14, status: "asked", session: "repl-71d0" },
{ t: "1h", tool: "http_post", arg: "hooks.slack.com/...", ms: 4, status: "denied", session: "web-2c91" }];


function ToolCallsCard({ onJump }) {
  return (
    <div className="card">
      <CardHeader
        title="最近工具调用"
        sub="7 条 · 24h"
        right={
        <>
            <button className="btn btn-ghost btn-sm"><Icon name="filter" size={13} />筛选</button>
            <button className="btn btn-secondary btn-sm">完整审计</button>
          </>
        } />
      
      <table className="tbl">
        <thead>
          <tr>
            <th style={{ width: 60 }}>时间</th>
            <th style={{ width: 130 }}>工具</th>
            <th>参数</th>
            <th style={{ width: 120 }}>会话</th>
            <th style={{ width: 100 }}>处理</th>
            <th style={{ width: 70, textAlign: "right" }}>耗时</th>
          </tr>
        </thead>
        <tbody>
          {TOOL_CALLS.map((c, i) =>
          <tr key={i}>
              <td className="t-mono-sm" style={{ color: "var(--ink-60)" }}>{c.t}</td>
              <td><span className="t-mono" style={{ color: "var(--ink)" }}>{c.tool}</span></td>
              <td className="t-mono" style={{ color: "var(--ink-80)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 360 }}>{c.arg}</td>
              <td className="t-mono-sm" style={{ color: "var(--ink-60)" }}>{c.session}</td>
              <td>
                {c.status === "ok" && <span className="chip chip-success"><Icon name="check" size={11} />已允许</span>}
                {c.status === "asked" && <span className="chip chip-info">询问后允许</span>}
                {c.status === "denied" && <span className="chip chip-danger"><Icon name="x" size={11} />已拒绝</span>}
              </td>
              <td className="col-num">{c.ms}ms</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>);

}

function SkillsPreview({ onJump }) {
  const skills = [
  { name: "video-editor", hits: 7, desc: "视频脚本拆解 / 四拍结构改写" },
  { name: "wechat-style", hits: 0, desc: "微信通道短回复风格调整", muted: true },
  { name: "example-skill", hits: 0, desc: "示例 skill · SKILL.md 协议演示" }];

  return (
    <div className="card">
      <CardHeader
        title="技能"
        sub="3 个已注册 · 匹配方式: 关键词"
        right={<button className="btn btn-ghost btn-sm" onClick={() => onJump("skills")}>管理 →</button>} />
      
      <div style={{ padding: 4 }}>
        {skills.map((s, i) =>
        <div key={s.name} style={{ display: "flex", alignItems: "center", padding: "12px 16px", borderTop: i === 0 ? "none" : "1px solid var(--divider-soft)" }}>
            <span className="dot" style={{ background: s.muted ? "var(--ink-30)" : "var(--primary)" }} />
            <div style={{ marginLeft: 12, flex: 1 }}>
              <div className="t-mono" style={{ color: s.muted ? "var(--ink-60)" : "var(--ink)" }}>{s.name}</div>
              <div className="t-meta" style={{ marginTop: 2 }}>{s.desc}</div>
            </div>
            <span className="t-mono-sm" style={{ color: s.hits ? "var(--primary)" : "var(--ink-60)" }}>{s.hits} 命中 / 24h</span>
          </div>
        )}
      </div>
    </div>);

}

function MemoryPreview({ onJump }) {
  const mem = [
  { path: "CLAUDE.md", scope: "project", chars: "4,210", hits: 18, primary: true },
  { path: "user/preferred_format.md", scope: "user", chars: "612", hits: 18 },
  { path: "project/jx-style-guide.md", scope: "project", chars: "1,820", hits: 9 },
  { path: "skill/video-editor.recipes", scope: "skill", chars: "5,108", hits: 4 }];

  return (
    <div className="card">
      <CardHeader
        title="记忆"
        sub="memdir · CLAUDE.md · ~/.sanshiliu/"
        right={<button className="btn btn-ghost btn-sm" onClick={() => onJump("memory")}>浏览 →</button>} />
      
      <div style={{ padding: 4 }}>
        {mem.map((m, i) =>
        <div key={m.path} style={{ display: "grid", gridTemplateColumns: "1fr 70px 60px 90px", gap: 10, alignItems: "center", padding: "12px 16px", borderTop: i === 0 ? "none" : "1px solid var(--divider-soft)" }}>
            <div className="t-mono" style={{ color: m.primary ? "var(--primary)" : "var(--ink)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.path}</div>
            <span className="chip" style={{ justifySelf: "start" }}>{m.scope}</span>
            <span className="t-mono-sm" style={{ color: "var(--ink-60)", textAlign: "right" }}>{m.chars}</span>
            <span className="t-mono-sm" style={{ color: m.hits ? "var(--primary)" : "var(--ink-60)", textAlign: "right" }}>{m.hits} 命中</span>
          </div>
        )}
      </div>
    </div>);

}

Object.assign(window, { Overview });