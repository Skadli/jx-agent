/* Channels — 真实读 /api/channels，配置写入由 setting.json 走（暂时只展示）。 */

function Channels({ onJump }) {
  const [active, setActive]     = React.useState("web");
  const [channels, setChannels] = React.useState(null);
  const [sessions, setSessions] = React.useState([]);
  const [health, setHealth]     = React.useState(null);

  const refresh = React.useCallback(async () => {
    const [c, s, h] = await Promise.all([
      API.get("/api/channels"),
      API.get("/api/sessions?limit=20"),
      API.get("/api/health"),
    ]);
    if (!c.error) setChannels(c);
    if (!s.error) setSessions(s.sessions || []);
    if (!h.error) setHealth(h);
  }, []);

  React.useEffect(() => {
    refresh();
    const id = setInterval(refresh, 10000);
    return () => clearInterval(id);
  }, [refresh]);

  // 按通道统计活跃会话
  const byChannel = sessions.reduce((acc, s) => {
    acc[s.channel] = acc[s.channel] || { sessions: 0, calls: 0 };
    acc[s.channel].sessions += 1;
    acc[s.channel].calls += s.calls || 0;
    return acc;
  }, {});

  const repl = channels && channels.repl;
  const web = channels && channels.web;
  const wechat = channels && channels.wechat;

  const reload = async () => {
    const r = await API.post("/api/instance/reload");
    if (r.error) alert("重载失败：" + r.error);
    else alert("已重载");
  };

  return (
    <div data-screen-label="06 通道">
      <PageHeader
        title="通道"
        sub={channels
          ? `${[repl && repl.enabled, web && web.enabled, wechat && wechat.enabled].filter(Boolean).length}/3 已启用 · OpenAI 兼容直通`
          : "加载中…"}
        actions={
          <>
            <button className="btn btn-secondary" onClick={() => onJump("permissions")}><Icon name="external" size={13}/>查看 settings.json</button>
            <button className="btn btn-secondary" onClick={reload}><Icon name="refresh" size={13}/>重启实例</button>
          </>
        }
      />

      <div className="page-body">
        <div className="grid-3">
          <ChannelSummary
            active={active === "repl"}
            onClick={() => setActive("repl")}
            icon="terminal" name="REPL" tag="本地终端"
            status={repl ? (repl.enabled ? "up" : "off") : "off"}
            sessions={byChannel.repl ? byChannel.repl.sessions : 0}
            msg24={byChannel.repl ? byChannel.repl.calls : 0}
            kv={[["状态", repl ? (repl.enabled ? "已启用" : "关闭") : "—"]]} />
          <ChannelSummary
            active={active === "web"}
            onClick={() => setActive("web")}
            icon="globe" name="Web HTTP" tag="/chat SSE · OpenAI 兼容"
            status={web ? "up" : "off"}
            sessions={byChannel.web ? byChannel.web.sessions : 0}
            msg24={byChannel.web ? byChannel.web.calls : 0}
            kv={web ? [["监听", `${web.host}:${web.port}`]] : [["状态", "—"]]} />
          <ChannelSummary
            active={active === "wechat"}
            onClick={() => setActive("wechat")}
            icon="wechat" name="iLink 微信" tag="扫码登录 · 消息中继"
            status={wechat && wechat.enabled ? "up" : "off"}
            sessions={byChannel.wechat ? byChannel.wechat.sessions : 0}
            msg24={byChannel.wechat ? byChannel.wechat.calls : 0}
            kv={wechat ? [["状态", wechat.enabled ? "已启用" : "未开启"], ["凭据", wechat.has_official_creds || wechat.has_webhook_creds ? "已配置" : "缺"]] : [["状态", "—"]]}
          />
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 360px", gap: 16, marginTop: 16 }}>
          <div className="card">
            <CardHeader
              title={active === "repl" ? "REPL · 配置" : active === "web" ? "Web HTTP · 配置" : "iLink 微信 · 配置"}
              sub={active === "repl" ? "本地终端 · 单进程" : active === "web" ? "/chat SSE 流式" : "扫码登录 · 消息中继"}
            />
            <div className="card-body">
              {active === "repl"   && <ReplConfig />}
              {active === "web"    && <WebConfig web={web} />}
              {active === "wechat" && <WechatConfig wechat={wechat} />}
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <LiveMetricsCard active={active} byChannel={byChannel} />
            <ProbesCard active={active} health={health} />
            <ModelCard onJump={onJump} />
          </div>
        </div>
      </div>
    </div>
  );
}

function ChannelSummary({ active, onClick, icon, name, tag, status, sessions, msg24, kv }) {
  const off = status === "off";
  return (
    <div className="card" onClick={onClick} style={{
      cursor: "pointer",
      borderColor: active ? "var(--primary)" : "var(--hairline)",
      borderWidth: active ? 2 : 1,
      padding: active ? 19 : 20,
    }}>
      <div className="card-padded" style={{ padding: 0 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{
              width: 32, height: 32, borderRadius: 8,
              background: off ? "var(--pearl)" : "var(--ink)",
              color: off ? "var(--ink-60)" : "#fff",
              display: "inline-flex", alignItems: "center", justifyContent: "center",
            }}><Icon name={icon} size={16} color={off ? "var(--ink-60)" : "#fff"}/></span>
            <div>
              <div className="t-card-title">{name}</div>
              <div className="t-meta">{tag}</div>
            </div>
          </div>
          <span className={`chip chip-dot ${status === "up" ? "chip-success" : ""}`}
                style={off ? { color: "var(--ink-60)" } : {}}>
            {status === "up" ? "正常" : "未开启"}
          </span>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 16 }}>
          <div>
            <div className="t-stat-sm">{sessions}</div>
            <div className="t-meta" style={{ marginTop: 2 }}>活跃会话</div>
          </div>
          <div>
            <div className="t-stat-sm">{msg24}</div>
            <div className="t-meta" style={{ marginTop: 2 }}>累计调用</div>
          </div>
        </div>

        <div style={{ marginTop: 16, paddingTop: 14, borderTop: "1px solid var(--hairline)", display: "flex", flexDirection: "column", gap: 6 }}>
          {kv.map(([k, v]) => <KV key={k} k={k} v={v} />)}
        </div>
      </div>
    </div>
  );
}

function ReplConfig() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="t-body" style={{ color: "var(--ink-60)" }}>
        REPL 通道在终端运行：<code className="t-mono-sm">python -m sanshiliu repl</code>。配置走 .env 文件。
      </div>
      <KV k="启动命令" v="python -m sanshiliu repl" />
      <KV k="data 目录" v="./data" />
      <KV k="持久化" v="sqlite + jsonl" />
    </div>
  );
}

function WebConfig({ web }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="t-body" style={{ color: "var(--ink-60)" }}>
        当前 web server 正在监听。修改端口需改 .env <code className="t-mono-sm">SANSHILIU_WEB_PORT</code> 并重启。
      </div>
      <KV k="监听地址" v={web ? `${web.host}:${web.port}` : "—"} />
      <KV k="状态" v={web ? web.status : "—"} accent="var(--success-fg)" />
      <KV k="端点" v="/chat (SSE) · /healthz · /metrics · /api/*" />
      <KV k="静态" v="/dashboard/*" />
    </div>
  );
}

function WechatConfig({ wechat }) {
  if (!wechat) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {!wechat.enabled && (
        <div className="card" style={{ background: "var(--warning-bg)", borderColor: "rgba(242,180,65,0.40)", padding: "10px 14px" }}>
          <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
            <Icon name="alert" size={16} color="var(--warning-fg)"/>
            <div>
              <div className="t-row-strong" style={{ color: "var(--warning-fg)" }}>未开启</div>
              <div className="t-meta" style={{ color: "var(--warning-fg)", marginTop: 2 }}>
                在 .env 设置 SANSHILIU_WECHAT_ENABLED=true 并填入凭据，或运行 <code className="t-mono-sm">python -m sanshiliu setup</code> 扫码。
              </div>
            </div>
          </div>
        </div>
      )}
      <KV k="启用" v={wechat.enabled ? "是" : "否"} />
      <KV k="官方 iLink 凭据" v={wechat.has_official_creds ? "已配置" : "缺"} />
      <KV k="Webhook 凭据" v={wechat.has_webhook_creds ? "已配置" : "缺"} />
    </div>
  );
}

function LiveMetricsCard({ active, byChannel }) {
  const stats = byChannel[active] || { sessions: 0, calls: 0 };
  return (
    <div className="card">
      <CardHeader title="实时" sub="每 10s 刷新" right={<span className="t-mono-sm" style={{ color: "var(--ink-60)" }}>实时</span>} />
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div>
          <div className="t-eyebrow">活跃会话</div>
          <div className="t-stat" style={{ marginTop: 4 }}>{stats.sessions}</div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
          <div>
            <div className="t-eyebrow">累计调用</div>
            <div className="t-stat-sm" style={{ marginTop: 4 }}>{stats.calls}</div>
          </div>
          <div>
            <div className="t-eyebrow">通道</div>
            <div className="t-stat-sm" style={{ marginTop: 4 }}>{active}</div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ProbesCard({ active, health }) {
  const comp = (health && health.components) || {};
  const rows = [
    { label: "Web",     status: comp.web === "up" ? "up" : "warn", value: comp.web || "?" },
    { label: "DB",      status: comp.db === "up" ? "up" : "warn",  value: comp.db || "?" },
    { label: "LLM",     status: comp.llm === "up" ? "up" : "warn", value: comp.llm || "?" },
    { label: "微信",     status: comp.wechat === "up" ? "up" : (comp.wechat === "disabled" ? "off" : "warn"), value: comp.wechat || "?" },
  ];
  return (
    <div className="card">
      <CardHeader title="探针" />
      <div className="card-body" style={{ paddingTop: 4 }}>
        {rows.map(r => <StatusRow key={r.label} {...r} />)}
      </div>
    </div>
  );
}

function ModelCard({ onJump }) {
  const [model, setModel] = React.useState("");
  React.useEffect(() => {
    API.get("/api/overview").then(o => { if (!o.error) setModel(o.model || "—"); });
  }, []);
  return (
    <div className="card">
      <CardHeader title="当前模型" />
      <div className="card-body">
        <div className="t-stat-sm">{model || "—"}</div>
        <div className="t-mono-sm" style={{ color: "var(--ink-60)", marginTop: 4 }}>OpenAI 兼容后端</div>
        <div style={{ display: "flex", gap: 6, marginTop: 14, flexWrap: "wrap" }}>
          <span className="chip">流式</span>
          <span className="chip">tool_calls</span>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { Channels });
