/* Permissions — admin settings.json surface.
 * Page header + segmented (rules / json) + three rule columns + recent prompts log.
 */

const RULES = {
  allow: [
    { tool: "file_read",       scope: "~/**",          desc: "读取所有本地文件" },
    { tool: "web_search",      scope: "*",             desc: "联网搜索" },
    { tool: "web_fetch",       scope: "https://*",     desc: "HTTPS 抓取" },
    { tool: "bash_exec",       scope: "ls · cat · grep · head · tail · wc", desc: "只读 shell" },
    { tool: "code_interpreter",scope: "python",        desc: "沙箱内 Python" },
    { tool: "file_read",       scope: "./persona/**",  desc: "人设目录" },
  ],
  ask: [
    { tool: "file_write",      scope: "./**",          desc: "项目目录写文件" },
    { tool: "bash_exec",       scope: "git *",         desc: "Git 操作" },
    { tool: "http_post",       scope: "api.openai.com", desc: "下游 LLM 之外的 POST" },
  ],
  deny: [
    { tool: "bash_exec",       scope: "rm · sudo · curl · wget", desc: "破坏 / 网络写" },
    { tool: "file_write",      scope: "~/.ssh/**",     desc: "SSH 凭据" },
    { tool: "file_write",      scope: "/etc/**",       desc: "系统配置" },
    { tool: "http_post",       scope: "*",             desc: "默认拒所有 POST" },
  ],
};

const SETTINGS_JSON = `{
  "version": "1.0.0-rc1",
  "model": {
    "provider": "openai-compatible",
    "base_url": "https://api.deepseek.com/v1",
    "model": "gpt-4o-mini",
    "api_key": "sk-•••••••"
  },
  "permissions": {
    "default_mode": "ask",
    "allow": [
      "file_read::~/**",
      "web_search::*",
      "web_fetch::https://*",
      "bash_exec::ls|cat|grep|head|tail|wc",
      "code_interpreter::python",
      "file_read::./persona/**"
    ],
    "ask": [
      "file_write::./**",
      "bash_exec::git *",
      "http_post::api.openai.com"
    ],
    "deny": [
      "bash_exec::rm|sudo|curl|wget",
      "file_write::~/.ssh/**",
      "file_write::/etc/**",
      "http_post::*"
    ]
  },
  "channels": {
    "repl":   { "enabled": true,  "prompt": "贱笑> " },
    "web":    { "enabled": true,  "host": "0.0.0.0", "port": 8080 },
    "wechat": { "enabled": false }
  },
  "persona_dir": "./persona",
  "memdir":      "~/.sanshiliu/memdir"
}`;

const PROMPTS = [
  { t: "2 分钟前",  tool: "file_write", target: "./persona/style.md",            outcome: "approved", ms: 1820, who: "操作者", session: "repl-8f2a" },
  { t: "14 分钟前", tool: "bash_exec",  target: "git commit -m 'tweak persona'", outcome: "approved", ms: 940,  who: "操作者", session: "repl-71d0" },
  { t: "1 小时前",  tool: "http_post",  target: "hooks.slack.com/services/...",  outcome: "denied",   ms: 4,    who: "agent",   session: "web-2c91" },
  { t: "3 小时前",  tool: "file_write", target: "/etc/hosts",                     outcome: "denied",   ms: 12,   who: "agent",   session: "repl-71d0" },
  { t: "昨天",      tool: "bash_exec",  target: "rm -rf node_modules",           outcome: "denied",   ms: 14,   who: "agent",   session: "web-44ce" },
  { t: "昨天",      tool: "file_write", target: "./scripts/v3.txt",              outcome: "approved", ms: 720,  who: "操作者", session: "repl-71d0" },
];

function Permissions({ onJump }) {
  const [mode, setMode] = React.useState("rules"); // rules | json
  const [defaultMode, setDefaultMode] = React.useState("ask");

  return (
    <div data-screen-label="07 权限">
      <PageHeader
        title="权限"
        sub="settings.json · 13 条规则 · 默认模式: 询问 · 协议对齐 Claude Code"
        actions={
          <>
            <Segmented value={mode} onChange={setMode} options={[
              { id: "rules", label: "规则视图" },
              { id: "json",  label: "settings.json" },
            ]} />
            <button className="btn btn-secondary"><Icon name="external" size={13}/>从 ~/.claude 导入</button>
            <button className="btn btn-primary"><Icon name="check" size={13} color="#fff"/>保存配置</button>
          </>
        }
      />

      <div className="page-body">
        {/* Stat row */}
        <div className="grid-4">
          <StatCard label="允许规则"     value={RULES.allow.length}  sub="自动放行"        color="var(--success-fg)" />
          <StatCard label="询问规则"     value={RULES.ask.length}    sub="每次先问"        color="var(--primary)" />
          <StatCard label="拒绝规则"     value={RULES.deny.length}   sub="直接挡掉"        color="var(--danger-fg)" />
          <StatCard label="24h 询问"     value="17"                   sub="批准 14 · 拒绝 3" trend={{kind:"flat", value:"持平"}} />
        </div>

        {/* Default-mode + last-saved bar */}
        <div className="card" style={{ marginTop: 16, padding: "12px 18px", display: "flex", alignItems: "center", gap: 16 }}>
          <div className="t-eyebrow" style={{ marginRight: 4 }}>默认模式</div>
          <Segmented value={defaultMode} onChange={setDefaultMode} options={[
            { id: "allow", label: "全部允许" },
            { id: "ask",   label: "询问" },
            { id: "deny",  label: "默认拒绝" },
          ]} />
          {defaultMode === "allow" && (
            <span className="chip chip-warning"><Icon name="alert" size={11}/>注意：不建议生产环境使用</span>
          )}
          <div className="grow" />
          <span className="t-meta">上次保存 · 2026-05-23 13:48 · 操作者 JX</span>
        </div>

        {mode === "rules" ? (
          <>
            <div className="grid-3" style={{ marginTop: 16 }}>
              <RuleColumn title="允许" desc="自动放行"  tone="success" rules={RULES.allow} />
              <RuleColumn title="询问" desc="每次先问"  tone="primary" rules={RULES.ask} />
              <RuleColumn title="拒绝" desc="直接挡掉"  tone="danger"  rules={RULES.deny} />
            </div>

            <div style={{ marginTop: 16 }}>
              <PromptLog />
            </div>
          </>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 320px", gap: 16, marginTop: 16 }}>
            <div className="card" style={{ overflow: "hidden" }}>
              <CardHeader
                title="~/.sanshiliu/settings.json"
                right={
                  <>
                    <button className="btn btn-ghost btn-sm">校验</button>
                    <button className="btn btn-ghost btn-sm">格式化</button>
                    <button className="btn btn-secondary btn-sm"><Icon name="copy" size={13}/>复制</button>
                  </>
                }
              />
              <pre style={{
                margin: 0,
                padding: "20px 24px",
                fontFamily: "var(--font-mono)",
                fontSize: 12.5,
                lineHeight: 1.75,
                color: "var(--ink-80)",
                whiteSpace: "pre",
                background: "var(--canvas)",
                overflow: "auto",
                maxHeight: "60vh",
              }}>{SETTINGS_JSON}</pre>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              <div className="card">
                <CardHeader title="结构验证" />
                <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 8, paddingTop: 4 }}>
                  <ValidRow ok label="JSON 语法" v="通过" />
                  <ValidRow ok label="version"   v="1.0.0-rc1" />
                  <ValidRow ok label="model.base_url" v="可解析" />
                  <ValidRow ok label="permissions" v={`${RULES.allow.length + RULES.ask.length + RULES.deny.length} 条`} />
                  <ValidRow ok label="持久化路径" v="~/.sanshiliu/" />
                </div>
              </div>
              <div className="card">
                <CardHeader title="Claude Code 兼容" />
                <div className="card-body">
                  <p className="t-body" style={{ margin: 0, marginBottom: 12 }}>
                    schema 完全一致；把 <code className="t-mono-sm">~/.claude/settings.json</code> 拷过来即可。
                  </p>
                  <button className="btn btn-secondary btn-sm" style={{ width: "100%" }}>从 Claude Code 导入</button>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function RuleColumn({ title, desc, tone, rules }) {
  const color = tone === "success" ? "var(--success-fg)" : tone === "primary" ? "var(--primary)" : "var(--danger-fg)";
  const bg    = tone === "success" ? "var(--success-bg)" : tone === "primary" ? "var(--primary-soft)" : "var(--danger-bg)";
  return (
    <div className="card">
      <div className="card-header" style={{ borderBottom: 0, padding: "16px 20px 0" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, background: color }} />
            <span className="t-card-title">{title}</span>
            <span className="chip" style={{ background: bg, color, fontWeight: 500 }}>{rules.length}</span>
          </div>
          <div className="t-meta" style={{ marginTop: 4 }}>{desc}</div>
        </div>
        <button className="btn-icon"><Icon name="plus" size={14}/></button>
      </div>
      <div style={{ padding: "12px 16px 16px", display: "flex", flexDirection: "column", gap: 8 }}>
        {rules.map((r, i) => (
          <div key={i} style={{
            padding: "10px 12px",
            background: "var(--pearl)",
            border: "1px solid var(--hairline)",
            borderRadius: 8,
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
              <span className="t-mono-strong" style={{ color: "var(--ink)" }}>{r.tool}</span>
              <button className="btn-icon" style={{ width: 22, height: 22 }}><Icon name="more" size={12}/></button>
            </div>
            <div className="t-mono-sm" style={{ color, marginTop: 6, wordBreak: "break-all" }}>{r.scope}</div>
            <div className="t-meta" style={{ marginTop: 6 }}>{r.desc}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function PromptLog() {
  return (
    <div className="card">
      <CardHeader
        title="询问日志"
        sub="近 24h · 17 次询问"
        right={
          <>
            <button className="btn btn-ghost btn-sm"><Icon name="filter" size={13}/>筛选</button>
            <button className="btn btn-secondary btn-sm"><Icon name="download" size={13}/>JSONL</button>
          </>
        }
      />
      <table className="tbl">
        <thead>
          <tr>
            <th style={{ width: 100 }}>时间</th>
            <th style={{ width: 140 }}>工具</th>
            <th>目标</th>
            <th style={{ width: 140 }}>会话</th>
            <th style={{ width: 80 }}>发起</th>
            <th style={{ width: 110 }}>处理</th>
            <th style={{ width: 80, textAlign: "right" }}>耗时</th>
          </tr>
        </thead>
        <tbody>
          {PROMPTS.map((p, i) => (
            <tr key={i}>
              <td className="t-mono-sm" style={{ color: "var(--ink-60)" }}>{p.t}</td>
              <td><span className="t-mono">{p.tool}</span></td>
              <td className="t-mono-sm" style={{ color: "var(--ink-80)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 360 }}>{p.target}</td>
              <td className="t-mono-sm" style={{ color: "var(--ink-60)" }}>{p.session}</td>
              <td className="t-row" style={{ color: "var(--ink-80)" }}>{p.who}</td>
              <td>
                {p.outcome === "approved"
                  ? <span className="chip chip-success"><Icon name="check" size={11}/>已允许</span>
                  : <span className="chip chip-danger"><Icon name="x" size={11}/>已拒绝</span>}
              </td>
              <td className="col-num">{p.ms}ms</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ValidRow({ ok, label, v }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
        <Icon name="check" size={13} color={ok ? "var(--success)" : "var(--danger)"} />
        <span className="t-row" style={{ color: "var(--ink)" }}>{label}</span>
      </span>
      <span className="t-mono-sm" style={{ color: "var(--ink-60)" }}>{v}</span>
    </div>
  );
}

Object.assign(window, { Permissions });
