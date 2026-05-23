/* Memory — memdir + CLAUDE.md.
 * File tree (scope-filtered) + reader + recent-hits panel. No marketing hero.
 */

const MEMDIR = [
  { id: "user/preferred_format.md",    scope: "user",    chars: 612,  mtime: "今天",      hit: 18, body: "# 默认输出格式偏好\n\n- 短句优先\n- 不写「希望对你有帮助」\n- 列表 ≤ 5 行" },
  { id: "user/banned_words.md",        scope: "user",    chars: 412,  mtime: "今天",      hit: 12, body: "# 禁词\n\n× 作为一个 AI\n× 您\n× 让我们一起\n× 希望对你有帮助" },
  { id: "project/jx-style-guide.md",   scope: "project", chars: 1820, mtime: "昨天",      hit: 9,  body: "# jx-agent 风格指南\n\n## 标题\n\n- 数字版本号 + 强设定\n- 「请勿」「最强」「年度总结」\n\n## 节奏\n\n短-短-长 三段式。" },
  { id: "project/launch_checklist.md", scope: "project", chars: 2104, mtime: "3 天前",    hit: 2,  body: "# 上线前清单\n\n1. /healthz 通\n2. /metrics 有数据\n3. settings.json 中 deny 规则 ≥ 3" },
  { id: "skill/video-editor.recipes",  scope: "skill",   chars: 5108, mtime: "5 天前",    hit: 4,  body: "# video-editor 配方\n\n## 四拍结构\n\n钩子 → 反转 → 拆解 → 收尾" },
  { id: "session/repl-8f2a.summary",   scope: "session", chars: 412,  mtime: "2 分钟前",  hit: 0,  body: "# repl-8f2a 摘要\n\n用户在策划情侣吵架视频，希望避免翻车。\n建议改为「测评一下情侣吵架谁的话术更逆天」。" },
];

const CLAUDE_MD = `# 三十六贱笑 · 项目记忆

> ~/.sanshiliu/CLAUDE.md
> 每次新会话开头注入到 prompt。
> 协议与 Claude Code 完全对齐 —— 把 ~/.claude/CLAUDE.md 拖过来直接能用。

## 项目目标

帮我做 B 站短视频博主「三十六贱笑」的数字分身，主要场景：
- 想标题 / 改脚本
- 翻车判断 / 风险评估
- 微信通道里短回复
- 内部团队 REPL 里跑

## 必须遵守

- 别写「作为一个 AI」、别写「您」
- 节奏：短句 + 长句交替；结论先抛
- 不知道直说"不知道，公开资料没核到"

## 工具偏好

- web_search 之前先尝试自己想；想不到再搜
- file_write 之前必须 file_read 确认
- bash_exec 任何 destructive 操作必须先 ask`;

function Memory({ onJump }) {
  const [active, setActive] = React.useState("user/preferred_format.md");
  const [scope, setScope] = React.useState("all");
  const filtered = scope === "all" ? MEMDIR : MEMDIR.filter(m => m.scope === scope);
  const isClaude = active === "__claude_md__";
  const file = MEMDIR.find(m => m.id === active);

  return (
    <div data-screen-label="04 记忆" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <PageHeader
        title="记忆"
        sub="CLAUDE.md + memdir/ · 10,468 字 · 24h 命中 45 次"
        actions={
          <>
            <button className="btn btn-secondary"><Icon name="download" size={13}/>导出</button>
            <button className="btn btn-secondary"><Icon name="external" size={13}/>从 ~/.claude 导入</button>
            <button className="btn btn-primary"><Icon name="plus" size={13} color="#fff"/>新建记忆</button>
          </>
        }
      />

      <div style={{
        display: "grid",
        gridTemplateColumns: "320px 1fr 320px",
        gap: 16,
        padding: "16px 28px 24px",
        flex: 1,
        minHeight: 0,
      }}>
        {/* LEFT — file tree */}
        <div className="card" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--hairline)" }}>
            <div className="search-wrap">
              <span className="search-icon"><Icon name="search" size={13} color="var(--ink-48)"/></span>
              <input className="search" placeholder="搜索记忆"/>
            </div>
            <div style={{ display: "flex", gap: 4, marginTop: 10, flexWrap: "wrap" }}>
              {[
                ["all",     "全部",    MEMDIR.length],
                ["user",    "user",    MEMDIR.filter(m=>m.scope==="user").length],
                ["project", "project", MEMDIR.filter(m=>m.scope==="project").length],
                ["skill",   "skill",   MEMDIR.filter(m=>m.scope==="skill").length],
                ["session", "session", MEMDIR.filter(m=>m.scope==="session").length],
              ].map(([id, label, n]) => (
                <button key={id} onClick={() => setScope(id)}
                  className={`chip`}
                  style={{
                    border: 0,
                    cursor: "pointer",
                    background: scope === id ? "var(--ink)" : "rgba(0,0,0,0.05)",
                    color:      scope === id ? "#fff"      : "var(--ink-80)",
                  }}>{label} · {n}</button>
              ))}
            </div>
          </div>

          <div style={{ overflowY: "auto", flex: 1 }}>
            {/* CLAUDE.md pinned */}
            <div onClick={() => setActive("__claude_md__")} style={{
              padding: "12px 14px",
              borderLeft: isClaude ? "3px solid var(--primary)" : "3px solid transparent",
              borderBottom: "1px solid var(--divider-soft)",
              background: isClaude ? "var(--primary-soft)" : "transparent",
              cursor: "pointer",
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <Icon name="stack" size={13} color={isClaude ? "var(--primary)" : "var(--ink-60)"}/>
                  <span className="t-mono-strong" style={{ color: isClaude ? "var(--primary)" : "var(--ink)" }}>CLAUDE.md</span>
                </span>
                <span className="chip chip-info">常驻</span>
              </div>
              <div className="t-meta" style={{ marginTop: 6 }}>项目记忆 · 4,210 字 · 每次会话顶部注入</div>
            </div>

            {filtered.map(m => (
              <MemoryFileItem key={m.id} m={m} active={active === m.id} onClick={() => setActive(m.id)} />
            ))}
          </div>
        </div>

        {/* CENTER — reader */}
        <div className="card" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <div className="card-header">
            <div>
              <div className="t-mono-sm" style={{ color: "var(--ink-60)" }}>{isClaude ? "~/.sanshiliu/CLAUDE.md" : `memdir/${file.id}`}</div>
              <div className="t-card-title" style={{ marginTop: 3 }}>
                {isClaude ? "项目记忆（顶部注入）" : `${file.scope} scope · ${file.chars} 字 · ${file.hit} 次命中`}
              </div>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn btn-ghost btn-sm"><Icon name="copy" size={13}/>复制</button>
              <button className="btn btn-secondary btn-sm"><Icon name="edit" size={13}/>编辑</button>
              {!isClaude && <button className="btn btn-icon" title="删除"><Icon name="trash" size={14} color="var(--ink-60)"/></button>}
            </div>
          </div>

          <pre style={{
            margin: 0,
            padding: "24px 32px",
            fontFamily: "var(--font-mono)",
            fontSize: 12.5,
            lineHeight: 1.75,
            color: "var(--ink-80)",
            whiteSpace: "pre-wrap",
            background: "var(--canvas)",
            flex: 1,
            overflowY: "auto",
          }}>{isClaude ? CLAUDE_MD : file.body}</pre>
        </div>

        {/* RIGHT — hits / metadata */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16, overflow: "auto" }}>
          {!isClaude && (
            <div className="card">
              <CardHeader title="近期命中" sub="哪些会话引用了这条记忆" />
              <div className="card-body" style={{ padding: 0 }}>
                <table className="tbl">
                  <tbody>
                    <tr><td><span className="t-mono">repl-8f2a</span></td><td className="t-meta" style={{ textAlign: "right" }}>14 分钟前</td></tr>
                    <tr><td><span className="t-mono">web-2c91</span></td><td className="t-meta" style={{ textAlign: "right" }}>1 小时前</td></tr>
                    <tr><td><span className="t-mono">repl-71d0</span></td><td className="t-meta" style={{ textAlign: "right" }}>3 小时前</td></tr>
                    <tr><td><span className="t-mono">web-44ce</span></td><td className="t-meta" style={{ textAlign: "right" }}>昨天</td></tr>
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="card">
            <CardHeader title="元信息" />
            <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <KV k="路径"    v={isClaude ? "~/.sanshiliu/CLAUDE.md" : `memdir/${file.id}`} />
              <KV k="scope"   v={isClaude ? "project (顶部)" : file.scope} />
              <KV k="字数"    v={isClaude ? "4,210" : String(file.chars)} />
              <KV k="最近修改" v={isClaude ? "2 天前" : file.mtime} />
              <KV k="近 24h 命中" v={isClaude ? "18 次" : `${file.hit} 次`} accent={(isClaude || file.hit > 0) ? "var(--primary)" : undefined} />
            </div>
          </div>

          <UsageInsights isClaude={isClaude} file={file} />
        </div>
      </div>
    </div>
  );
}

function UsageInsights({ isClaude, file }) {
  const hit = isClaude ? 18 : file.hit;
  const trend = isClaude ? [12, 15, 14, 18, 16, 19, 18]
              : hit > 5  ? [2, 4, 3, 6, 5, 8, 7]
              : hit > 0  ? [1, 2, 1, 2, 3, 2, 3]
                         : [0, 1, 0, 0, 1, 0, 0];
  const trendMax = Math.max(...trend, 1);
  const trendLabel = isClaude ? "+8%" : hit > 5 ? "+22%" : hit > 0 ? "持平" : "—";
  const trendColor = isClaude || hit > 5 ? "var(--success-fg)" : "var(--ink-60)";

  const status      = isClaude ? "常驻注入" : hit > 5 ? "活跃" : hit > 0 ? "偶尔引用" : "本周未引用";
  const statusColor = isClaude || hit > 5 ? "var(--success-fg)" : hit === 0 ? "var(--warning-fg)" : undefined;
  const avgInject   = isClaude ? "4,210 tok" : `${Math.round((file.chars || 0) * 0.6).toLocaleString()} tok`;

  return (
    <div className="card">
      <CardHeader title="使用洞察" sub={isClaude ? "项目记忆全局指标" : "这条记忆最近一周表现"} />
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 }}>
            <span className="t-meta">7 天命中走势</span>
            <span className="t-mono-sm" style={{ color: trendColor }}>{trendLabel}</span>
          </div>
          <div style={{ display: "flex", alignItems: "flex-end", gap: 3, height: 32 }}>
            {trend.map((v, i) => (
              <div key={i} style={{
                flex: 1,
                height: `${Math.max(6, (v / trendMax) * 100)}%`,
                background: i === trend.length - 1 ? "var(--primary)" : "var(--primary-soft-2)",
                borderRadius: 2,
              }} />
            ))}
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 8, paddingTop: 10, borderTop: "1px solid var(--divider-soft)" }}>
          <KV k="状态"       v={status}   mono={false} accent={statusColor} />
          <KV k="平均注入"   v={avgInject} />
          <KV k="冲突检测"   v="无"        mono={false} />
        </div>
      </div>
    </div>
  );
}

function MemoryFileItem({ m, active, onClick }) {
  const scopeColors = {
    user:    { bg: "rgba(0,102,204,0.10)",  fg: "var(--primary)" },
    project: { bg: "rgba(48,162,114,0.10)", fg: "var(--success-fg)" },
    skill:   { bg: "rgba(193,60,123,0.10)", fg: "#9c2f5f" },
    session: { bg: "rgba(0,0,0,0.05)",      fg: "var(--ink-60)" },
  };
  const sc = scopeColors[m.scope] || scopeColors.session;
  return (
    <div onClick={onClick} style={{
      padding: "12px 14px",
      borderLeft: active ? "3px solid var(--primary)" : "3px solid transparent",
      borderBottom: "1px solid var(--divider-soft)",
      background: active ? "var(--primary-soft)" : "transparent",
      cursor: "pointer",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span className="t-mono-sm" style={{ color: active ? "var(--primary)" : "var(--ink)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.id}</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 6 }}>
        <span className="chip" style={{ background: sc.bg, color: sc.fg, fontSize: 10.5 }}>{m.scope}</span>
        <span className="t-meta">{m.chars} 字</span>
        <span className="t-meta">·</span>
        <span className="t-meta" style={{ color: m.hit ? "var(--primary)" : "var(--ink-60)" }}>{m.hit} 命中</span>
        <span className="t-meta" style={{ marginLeft: "auto" }}>{m.mtime}</span>
      </div>
    </div>
  );
}

Object.assign(window, { Memory });
