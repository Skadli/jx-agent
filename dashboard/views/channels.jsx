/* Channels — admin config surface.
 * Page header + channel selector (segmented) + config form + live metrics panel.
 */

function Channels({ onJump }) {
  const [active, setActive] = React.useState("web");

  return (
    <div data-screen-label="06 通道">
      <PageHeader
        title="通道"
        sub="3 个通道 · 2 已启用 · OpenAI 兼容直通"
        actions={
          <>
            <button className="btn btn-secondary"><Icon name="external" size={13}/>查看 settings.json</button>
            <button className="btn btn-primary"><Icon name="check" size={13} color="#fff"/>保存配置</button>
          </>
        }
      />

      <div className="page-body">
        {/* Three summary cards as selectable tabs */}
        <div className="grid-3">
          <ChannelSummary
            active={active === "repl"}
            onClick={() => setActive("repl")}
            icon="terminal" name="REPL" tag="本地终端"
            status="up" sessions={2} msg24={184}
            kv={[["进程", "PID 48211"], ["命令前缀", "/"]]}
          />
          <ChannelSummary
            active={active === "web"}
            onClick={() => setActive("web")}
            icon="globe" name="Web HTTP" tag="/chat SSE · OpenAI 兼容"
            status="up" sessions={5} msg24={624}
            kv={[["监听", "0.0.0.0:8080"], ["TLS", "letsencrypt"]]}
          />
          <ChannelSummary
            active={active === "wechat"}
            onClick={() => setActive("wechat")}
            icon="wechat" name="iLink 微信" tag="扫码登录 · 消息中继"
            status="off" sessions={0} msg24={0}
            kv={[["状态", "未开启"], ["缺", "iLink token"]]}
          />
        </div>

        {/* Detail */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 360px", gap: 16, marginTop: 16 }}>
          <div className="card">
            <CardHeader
              title={active === "repl" ? "REPL · 配置" : active === "web" ? "Web HTTP · 配置" : "iLink 微信 · 配置"}
              sub={active === "repl" ? "本地终端 · 单进程" : active === "web" ? "/chat SSE 流式" : "扫码登录 · 消息中继"}
              right={
                <>
                  <button className="btn btn-ghost btn-sm"><Icon name="refresh" size={13}/>重启</button>
                  <button className="btn btn-secondary btn-sm">回滚</button>
                </>
              }
            />
            <div className="card-body">
              {active === "repl"   && <ReplConfig />}
              {active === "web"    && <WebConfig />}
              {active === "wechat" && <WechatConfig />}
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <LiveMetricsCard active={active} />
            <ProbesCard active={active} />
            <ModelCard onJump={onJump} />
          </div>
        </div>

        {/* Connection log */}
        <div style={{ marginTop: 16 }}>
          <ConnectionLog active={active} />
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
      padding: active ? 19 : 20, // compensate
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
            <div className="t-meta" style={{ marginTop: 2 }}>24h 消息</div>
          </div>
        </div>

        <div style={{ marginTop: 16, paddingTop: 14, borderTop: "1px solid var(--hairline)", display: "flex", flexDirection: "column", gap: 6 }}>
          {kv.map(([k, v]) => <KV key={k} k={k} v={v} />)}
        </div>
      </div>
    </div>
  );
}

/* ===== Configurators ===== */

function FormField({ label, hint, children }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span className="t-row-strong">{label}</span>
        {hint && <span className="t-meta">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

function ToggleRow({ label, hint, defaultOn = true }) {
  const [on, setOn] = React.useState(defaultOn);
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 0", borderBottom: "1px solid var(--divider-soft)" }}>
      <div>
        <div className="t-row-strong">{label}</div>
        {hint && <div className="t-meta" style={{ marginTop: 2 }}>{hint}</div>}
      </div>
      <Toggle on={on} onChange={setOn} />
    </div>
  );
}

function ReplConfig() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <FormField label="启动命令">
        <input className="field field-mono" defaultValue="python -m sanshiliu repl" />
      </FormField>
      <FormField label="工作目录">
        <input className="field field-mono" defaultValue="~/.sanshiliu" />
      </FormField>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <FormField label="提示符">
          <input className="field field-mono" defaultValue="贱笑> " />
        </FormField>
        <FormField label="命令前缀">
          <input className="field field-mono" defaultValue="/" />
        </FormField>
      </div>
      <FormField label="历史记录" hint="≤10,000 行">
        <input className="field field-mono" defaultValue="~/.sanshiliu/history" />
      </FormField>
      <div style={{ marginTop: 6 }}>
        <ToggleRow label="自动重连断开会话" />
        <ToggleRow label="行内打印 token 计费" />
        <ToggleRow label="启动横幅" />
        <ToggleRow label="bash 历史互通" defaultOn={false} hint="zsh / bash 共享 history" />
      </div>
    </div>
  );
}

function WebConfig() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 16 }}>
        <FormField label="监听地址">
          <input className="field field-mono" defaultValue="0.0.0.0" />
        </FormField>
        <FormField label="端口">
          <input className="field field-mono" defaultValue="8080" />
        </FormField>
      </div>
      <FormField label="CORS Origins" hint="逗号分隔">
        <input className="field field-mono" defaultValue="https://*.sanshiliu.app, http://localhost:5173" />
      </FormField>
      <FormField label="API Key 头" hint="附加到 /chat 请求">
        <input className="field field-mono" defaultValue="X-API-Key" />
      </FormField>
      <FormField label="速率限制" hint="每 IP / 分钟">
        <input className="field field-mono" defaultValue="60" />
      </FormField>
      <div style={{ marginTop: 6 }}>
        <ToggleRow label="/chat SSE 流式" />
        <ToggleRow label="/healthz 公开" />
        <ToggleRow label="/metrics Prometheus 暴露" hint="text/plain · v0.0.4 schema" />
        <ToggleRow label="OpenAI 兼容模式" hint="messages[]、tool_calls" />
      </div>
    </div>
  );
}

function WechatConfig() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="card" style={{ background: "var(--warning-bg)", borderColor: "rgba(242,180,65,0.40)", padding: "10px 14px" }}>
        <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
          <Icon name="alert" size={16} color="var(--warning-fg)"/>
          <div>
            <div className="t-row-strong" style={{ color: "var(--warning-fg)" }}>未开启</div>
            <div className="t-meta" style={{ color: "var(--warning-fg)", marginTop: 2 }}>填入 iLink token 并扫码，三步内能跑通。</div>
          </div>
        </div>
      </div>

      <FormField label="iLink Token" hint="存在 secrets 里">
        <input className="field field-mono" placeholder="ilink_•••••••••••••••" />
      </FormField>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <FormField label="目标群">
          <input className="field field-mono" placeholder="群名 或 wxid" />
        </FormField>
        <FormField label="触发词">
          <input className="field field-mono" defaultValue="@贱笑" />
        </FormField>
      </div>

      <FormField label="每日上限">
        <input className="field field-mono" defaultValue="200" />
      </FormField>

      <div style={{ marginTop: 6 }}>
        <ToggleRow label="自动套用 wechat-style 技能" />
        <ToggleRow label="避免回复包含「您」" />
        <ToggleRow label="只回 @ 自己的消息" />
      </div>

      <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 8, padding: 14, background: "var(--pearl)", borderRadius: 10, border: "1px solid var(--hairline)" }}>
        <div style={{ width: 72, height: 72, border: "1px dashed var(--hairline-strong)", borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", background: "var(--canvas)" }}>
          <Icon name="qr" size={32} color="var(--ink-48)"/>
        </div>
        <div style={{ flex: 1 }}>
          <div className="t-row-strong">扫码登录</div>
          <div className="t-meta" style={{ marginTop: 3 }}>填好 token 后，这里会生成二维码。</div>
        </div>
        <button className="btn btn-primary btn-sm">生成二维码</button>
      </div>
    </div>
  );
}

/* ===== Live metrics ===== */

function LiveMetricsCard({ active }) {
  const m = {
    repl:   { s: "2",  c: "18", l: "—" },
    web:    { s: "5",  c: "47", l: "1.21" },
    wechat: { s: "0",  c: "0",  l: "—" },
  }[active];
  return (
    <div className="card">
      <CardHeader title="实时" sub="每 5s 刷新" right={<span className="t-mono-sm" style={{ color: "var(--ink-60)" }}>2s 前</span>} />
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div>
          <div className="t-eyebrow">活跃会话</div>
          <div className="t-stat" style={{ marginTop: 4 }}>{m.s}</div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
          <div>
            <div className="t-eyebrow">1h 消息</div>
            <div className="t-stat-sm" style={{ marginTop: 4 }}>{m.c}</div>
          </div>
          <div>
            <div className="t-eyebrow">首字延迟</div>
            <div className="t-stat-sm" style={{ marginTop: 4 }}>{m.l}{m.l !== "—" && <span className="t-row" style={{ color: "var(--ink-60)" }}> s</span>}</div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ProbesCard({ active }) {
  const off = active === "wechat";
  const rows = [
    { label: "/healthz",  status: off ? "off" : "up",   value: off ? "—" : "200 · 8ms" },
    { label: "/metrics",  status: off ? "off" : "up",   value: off ? "—" : "200 · 14ms" },
    { label: "LLM",       status: "up",                  value: "312ms" },
    { label: "模型缓存",   status: "up",                  value: "14 hit / 3 miss" },
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
  return (
    <div className="card">
      <CardHeader title="当前模型" right={<button className="btn btn-ghost btn-sm">切换 →</button>} />
      <div className="card-body">
        <div className="t-stat-sm">gpt-4o-mini</div>
        <div className="t-mono-sm" style={{ color: "var(--ink-60)", marginTop: 4 }}>api.deepseek.com / v1</div>
        <div style={{ display: "flex", gap: 6, marginTop: 14, flexWrap: "wrap" }}>
          <span className="chip">128k 上下文</span>
          <span className="chip">流式</span>
          <span className="chip">tool_calls</span>
        </div>
      </div>
    </div>
  );
}

/* ===== Connection log ===== */

const CONNS = {
  repl: [
    { t: "现在",        ev: "session.open",  meta: "repl-8f2a", note: "pid=48211" },
    { t: "14m",        ev: "command.exec",  meta: "/stats",    note: "OK 12ms" },
    { t: "3h",         ev: "session.open",  meta: "repl-71d0", note: "pid=48211" },
    { t: "3h",         ev: "session.close", meta: "repl-71d0", note: "/quit · 18 msgs · 11,408 tok" },
  ],
  web: [
    { t: "30s",  ev: "POST /chat",     meta: "web-2c91", note: "200 · text/event-stream · 1.18s" },
    { t: "2m",   ev: "POST /chat",     meta: "web-2c91", note: "200 · text/event-stream · 1.42s" },
    { t: "14m",  ev: "POST /chat",     meta: "web-2c91", note: "200 · 18,902 tok" },
    { t: "1h",   ev: "GET /healthz",   meta: "—",        note: "200 · 8ms · k8s liveness" },
    { t: "1h",   ev: "POST /chat",     meta: "denied",   note: "401 · X-API-Key 错误" },
    { t: "2h",   ev: "GET /metrics",   meta: "—",        note: "200 · 14ms · prom-scrape" },
  ],
  wechat: [
    { t: "—",   ev: "未启用",          meta: "—",        note: "提供 iLink token 后查看连接日志" },
  ],
};

function ConnectionLog({ active }) {
  const rows = CONNS[active];
  return (
    <div className="card">
      <CardHeader title="连接日志" sub={`通道 · ${active}`} right={<button className="btn btn-ghost btn-sm"><Icon name="download" size={13}/>导出</button>} />
      <table className="tbl">
        <thead>
          <tr>
            <th style={{ width: 80 }}>时间</th>
            <th style={{ width: 220 }}>事件</th>
            <th style={{ width: 140 }}>对象</th>
            <th>详情</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td className="t-mono-sm" style={{ color: "var(--ink-60)" }}>{r.t}</td>
              <td><span className="t-mono">{r.ev}</span></td>
              <td className="t-mono-sm" style={{ color: "var(--ink-60)" }}>{r.meta}</td>
              <td className="t-row" style={{ color: "var(--ink-80)" }}>{r.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

Object.assign(window, { Channels });
