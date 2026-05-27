/* Settings — 通过 /api/settings 读写 .env；分组：LLM / 微信 / Dashboard。
 * 敏感字段（api_key/token/password）在前端不展示原文，留空 = 不修改。
 */

function Settings() {
  const [data, setData] = React.useState(null);
  const [status, setStatus] = React.useState("loading");
  const [error, setError] = React.useState("");
  const [notice, setNotice] = React.useState("");
  const [saving, setSaving] = React.useState(false);

  // 表单缓冲；提交时合并为一个 PUT
  const [form, setForm] = React.useState({});
  const [secrets, setSecrets] = React.useState({});

  const refresh = React.useCallback(async () => {
    setStatus("loading");
    setError("");
    const r = await API.get("/api/settings");
    if (r.error) {
      setError(r.error);
      setStatus("error");
      return;
    }
    setData(r);
    setForm(r.values || {});
    setSecrets({});  // 每次刷新清空敏感字段输入
    setStatus("ready");
  }, []);

  React.useEffect(() => { refresh(); }, [refresh]);

  const updateForm = (key, value) => setForm(f => ({ ...f, [key]: value }));
  const updateSecret = (key, value) => setSecrets(s => ({ ...s, [key]: value }));

  const submit = async (group) => {
    setSaving(true);
    setNotice("");
    setError("");
    const payload = {};
    for (const k of group.plainKeys) {
      if (form[k] !== undefined) payload[k] = form[k];
    }
    for (const k of group.secretKeys) {
      const v = secrets[k];
      if (v && v.trim()) payload[k] = v;
    }
    const r = await API.put("/api/settings", payload);
    setSaving(false);
    if (r.error) {
      setError(r.error);
      return;
    }
    setNotice(`${group.title} 保存成功 · ${r.applied?.join(", ") || "无变化"}${r.note ? " · " + r.note : ""}`);
    refresh();
  };

  if (status === "loading") {
    return (
      <div data-screen-label="09 设置">
        <PageHeader title="设置" sub="加载中…" />
      </div>
    );
  }
  if (status === "error" || !data) {
    return (
      <div data-screen-label="09 设置">
        <PageHeader title="设置" sub="加载失败" />
        <div className="page-body">
          <div className="card card-padded" style={{ color: "var(--danger)" }}>
            读取失败：{error || "未知错误"}
          </div>
        </div>
      </div>
    );
  }

  const llmGroup = {
    title: "LLM 模型",
    plainKeys: ["openai_base_url", "openai_model"],
    secretKeys: ["openai_api_key"],
  };
  const multimodalGroup = {
    title: "多模态后端（豆包 vision-pro）",
    plainKeys: ["doubao_base_url", "doubao_model"],
    secretKeys: ["doubao_api_key"],
  };
  const wechatGroup = {
    title: "微信通道开关",
    plainKeys: ["wechat_enabled"],
    secretKeys: [],
  };
  const dashboardGroup = {
    title: "Dashboard 密码",
    plainKeys: [],
    secretKeys: ["dashboard_password"],
  };
  const logLevelGroup = {
    title: "日志级别",
    plainKeys: ["log_level"],
    secretKeys: [],
  };

  return (
    <div data-screen-label="09 设置">
      <PageHeader
        title="设置"
        sub={`配置文件 · ${data.env_path || ".env"}`}
        actions={
          <button className="btn btn-secondary" onClick={refresh}><Icon name="refresh" size={13} />重新加载</button>
        } />

      <div className="page-body">
        {error && (
          <div className="card card-padded" style={{ marginBottom: 16, color: "var(--danger)", borderColor: "var(--danger-bg)" }}>
            <Icon name="alert" size={14} color="var(--danger)" /> {error}
          </div>
        )}
        {notice && (
          <div className="card card-padded" style={{ marginBottom: 16, color: "var(--success-fg)", background: "var(--success-bg)", borderColor: "transparent" }}>
            <Icon name="check" size={14} color="var(--success-fg)" /> {notice}
          </div>
        )}

        {/* ── LLM ── */}
        <SettingsCard title="LLM 模型" subtitle="OpenAI 兼容后端 · 支持 DeepSeek/GLM/通义/OneAPI/Ollama 等">
          <Field
            label="Base URL"
            hint="末尾不带 / ；改后切换到对应供应商"
            value={form.openai_base_url || ""}
            placeholder="https://api.deepseek.com"
            onChange={v => updateForm("openai_base_url", v)} />
          <Field
            label="模型"
            hint="模型 ID；与 Base URL 后端约定"
            value={form.openai_model || ""}
            placeholder="deepseek-v4-flash"
            onChange={v => updateForm("openai_model", v)} />
          <SecretField
            label="API Key"
            placeholder="留空 = 保留当前值"
            existing={data.secrets?.openai_api_key}
            value={secrets.openai_api_key || ""}
            onChange={v => updateSecret("openai_api_key", v)} />
          <FormFooter saving={saving} onSave={() => submit(llmGroup)} />
        </SettingsCard>

        {/* ── 多模态后端 ── 豆包 vision-pro，缺则降级走 OPENAI_* */}
        <SettingsCard title="多模态后端（豆包 vision-pro）" subtitle="火山引擎 Ark，OpenAI 兼容；缺凭据则纯文本场景不受影响">
          <Field
            label="Base URL"
            hint="末尾不带 / ；默认 https://ark.cn-beijing.volces.com/api/v3"
            value={form.doubao_base_url || ""}
            placeholder="https://ark.cn-beijing.volces.com/api/v3"
            onChange={v => updateForm("doubao_base_url", v)} />
          <Field
            label="模型"
            hint="模型 ID 或 endpoint ID（ep-xxx）；vision-pro 系列"
            value={form.doubao_model || ""}
            placeholder="doubao-seed-2-0-pro-260215"
            onChange={v => updateForm("doubao_model", v)} />
          <SecretField
            label="API Key"
            placeholder="留空 = 保留当前值"
            existing={data.secrets?.doubao_api_key}
            value={secrets.doubao_api_key || ""}
            onChange={v => updateSecret("doubao_api_key", v)} />
          <FormFooter saving={saving} onSave={() => submit(multimodalGroup)} />
        </SettingsCard>

        {/* ── 微信 ── 扫码登录 + 启用开关 */}
        <SettingsCard title="微信通道" subtitle="扫码连接官方 iLink Bot；连接后才能启用此通道">
          <WechatConnect data={data} onChanged={refresh} />
          <div style={{ borderTop: "1px solid var(--hairline)", margin: "8px 0" }} />
          <ToggleRow
            label="启用微信 Bot"
            hint="必须先连接成功；切换后需要重启 sanshiliu serve 才能生效"
            on={!!form.wechat_enabled}
            onChange={v => updateForm("wechat_enabled", v)} />
          <FormFooter saving={saving} onSave={() => submit(wechatGroup)} />
        </SettingsCard>

        {/* ── Dashboard 密码 ── */}
        <SettingsCard title="Dashboard 密码" subtitle="管理后台进入密码；为空则禁用门禁">
          <SecretField
            label="新密码"
            placeholder="留空 = 保留当前值"
            existing={data.secrets?.dashboard_password}
            value={secrets.dashboard_password || ""}
            onChange={v => updateSecret("dashboard_password", v)} />
          <div className="t-meta" style={{ color: "var(--ink-60)" }}>
            修改后需要重启进程并重新登录。
          </div>
          <FormFooter saving={saving} onSave={() => submit(dashboardGroup)} />
        </SettingsCard>

        {/* ── 日志级别 ── 控制台 + JSONL 同步生效 */}
        <SettingsCard title="日志级别" subtitle="控制台 + JSONL 同步生效；改后需重启进程">
          <SelectRow
            label="日志级别"
            hint="DEBUG 最详尽 / INFO 默认 / WARNING 仅警告 / ERROR 仅错误 / CRITICAL 仅致命"
            value={(form.log_level || "INFO").toUpperCase()}
            options={["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]}
            onChange={v => updateForm("log_level", v)} />
          <FormFooter saving={saving} onSave={() => submit(logLevelGroup)} />
        </SettingsCard>

        <div className="t-meta" style={{ color: "var(--ink-60)", marginTop: 16, padding: "0 4px" }}>
          注：所有改动都会立即写入 <code className="t-mono">{data.env_path || ".env"}</code>。
          大部分字段（API key / base url / 模型 / 微信凭据 / 密码）需要重启 `python -m sanshiliu serve` 才能生效。
        </div>
      </div>
    </div>
  );
}

/* ──────────── 子组件 ──────────── */

function SettingsCard({ title, subtitle, children }) {
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <CardHeader title={title} sub={subtitle} />
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {children}
      </div>
    </div>
  );
}

function Field({ label, hint, value, placeholder, onChange }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(140px, 180px) minmax(0, 1fr)", gap: 16, alignItems: "start" }}>
      <div>
        <div className="t-body-strong">{label}</div>
        {hint && <div className="t-meta" style={{ color: "var(--ink-60)", marginTop: 4 }}>{hint}</div>}
      </div>
      <input
        className="field field-mono"
        value={value || ""}
        placeholder={placeholder || ""}
        onChange={e => onChange(e.target.value)} />
    </div>
  );
}

function SecretField({ label, placeholder, existing, value, onChange }) {
  const status = existing && existing.set
    ? <span className="chip chip-success" style={{ fontSize: 11 }}>已设置 · {existing.masked || "***"}</span>
    : <span className="chip chip-warning" style={{ fontSize: 11 }}>未设置</span>;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(140px, 180px) minmax(0, 1fr)", gap: 16, alignItems: "start" }}>
      <div>
        <div className="t-body-strong">{label}</div>
        <div style={{ marginTop: 4 }}>{status}</div>
      </div>
      <input
        className="field field-mono"
        type="password"
        autoComplete="new-password"
        value={value || ""}
        placeholder={placeholder || "留空 = 保留当前值"}
        onChange={e => onChange(e.target.value)} />
    </div>
  );
}

function SelectRow({ label, hint, value, options, onChange }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(140px, 180px) minmax(0, 1fr)", gap: 16, alignItems: "start" }}>
      <div>
        <div className="t-body-strong">{label}</div>
        {hint && <div className="t-meta" style={{ color: "var(--ink-60)", marginTop: 4 }}>{hint}</div>}
      </div>
      <select
        className="field field-mono"
        value={value || ""}
        onChange={e => onChange(e.target.value)}>
        {(options || []).map(opt => (
          <option key={opt} value={opt}>{opt}</option>
        ))}
      </select>
    </div>
  );
}

function ToggleRow({ label, hint, on, onChange }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(140px, 180px) minmax(0, 1fr)", gap: 16, alignItems: "center" }}>
      <div>
        <div className="t-body-strong">{label}</div>
        {hint && <div className="t-meta" style={{ color: "var(--ink-60)", marginTop: 4 }}>{hint}</div>}
      </div>
      <div>
        <Toggle on={on} onChange={onChange} />
      </div>
    </div>
  );
}

function FormFooter({ saving, onSave }) {
  return (
    <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 6 }}>
      <button className="btn btn-primary" disabled={saving} onClick={onSave}>
        {saving ? "保存中…" : "保存到 .env"}
      </button>
    </div>
  );
}

/* ─────────── 微信扫码连接组件 ─────────── */

function WechatConnect({ data, onChanged }) {
  const accountId = data?.values?.weixin_account_id || "";
  const tokenSet  = !!data?.secrets?.weixin_token?.set;
  const connected = !!accountId && tokenSet;

  const [session, setSession]   = React.useState(null);  // {session_id, qr_data_url, status, ...}
  const [error, setError]       = React.useState("");
  const [starting, setStarting] = React.useState(false);
  const [healthStatus, setHealthStatus] = React.useState(null);  // "up" | "expired" | "down" | ...
  const pollTimer = React.useRef(null);

  // 拉一次健康状态判断 token 是否已过期
  React.useEffect(() => {
    let alive = true;
    const ping = async () => {
      const r = await API.get("/api/health");
      if (!alive || r.error) return;
      setHealthStatus(r.components?.wechat || null);
    };
    ping();
    const id = setInterval(ping, 10000);
    return () => { alive = false; clearInterval(id); };
  }, []);
  const expired = connected && healthStatus === "expired";

  const stopPoll = React.useCallback(() => {
    if (pollTimer.current) {
      clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  const tick = React.useCallback(async (sid) => {
    const r = await API.get(`/api/wechat/qr/status?session=${encodeURIComponent(sid)}`);
    if (r.error) {
      setError(r.error);
      stopPoll();
      return;
    }
    setSession(r);
    if (r.status === "confirmed") {
      stopPoll();
      // 让父组件 refresh 重新拉 settings 显示新连接状态
      setTimeout(() => onChanged && onChanged(), 600);
    } else if (["timeout", "expired", "cancelled", "error"].includes(r.status)) {
      stopPoll();
    }
  }, [stopPoll, onChanged]);

  const start = async () => {
    setError("");
    setStarting(true);
    stopPoll();
    const r = await API.post("/api/wechat/qr/start", {});
    setStarting(false);
    if (r.error) {
      setError(r.error);
      return;
    }
    setSession(r);
    pollTimer.current = setInterval(() => tick(r.session_id), 1500);
  };

  const cancel = async () => {
    stopPoll();
    if (session?.session_id) {
      await API.post("/api/wechat/qr/cancel", { session_id: session.session_id });
    }
    setSession(null);
  };

  React.useEffect(() => {
    return () => stopPoll();
  }, [stopPoll]);

  // 当前连接状态展示：未连接 / 已连接 / 已过期
  let statusBlock;
  if (!connected) {
    statusBlock = (
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span className="chip chip-warning chip-dot">未连接</span>
        <span className="t-meta" style={{ color: "var(--ink-60)" }}>点击下方按钮扫码登录</span>
      </div>
    );
  } else if (expired) {
    statusBlock = (
      <div style={{
        padding: "10px 12px", background: "var(--danger-bg)", borderRadius: 8,
        display: "flex", flexDirection: "column", gap: 6,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span className="chip chip-danger chip-dot">会话已过期</span>
          <span className="t-mono-sm" style={{ color: "var(--ink)" }}>{accountId}</span>
        </div>
        <div className="t-meta" style={{ color: "var(--danger-fg)" }}>
          iLink 返回 -14 session timeout —— token 已失效。请点击下方"重新扫码连接"重新登录；
          保存后需要重启 <code className="t-mono">sanshiliu serve</code> 才能让新 token 生效。
        </div>
      </div>
    );
  } else {
    statusBlock = (
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span className="chip chip-success chip-dot">已连接</span>
        <span className="t-mono-sm" style={{ color: "var(--ink)" }}>{accountId}</span>
      </div>
    );
  }

  // 没在扫码：展示状态 + 入口按钮
  if (!session) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {statusBlock}
        {error && (
          <div className="t-meta" style={{ color: "var(--danger)" }}>
            <Icon name="alert" size={12} color="var(--danger)" /> {error}
          </div>
        )}
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn btn-primary" disabled={starting} onClick={start}>
            <Icon name="qr" size={13} color="#fff" />
            {starting ? "生成二维码…" : connected ? "重新扫码连接" : "扫码连接微信"}
          </button>
        </div>
      </div>
    );
  }

  // 正在扫码：显示二维码 + 状态 + 操作
  const confirmed = session.status === "confirmed";
  const ended = ["confirmed", "timeout", "expired", "cancelled", "error"].includes(session.status);
  const statusColor = confirmed ? "var(--success-fg)"
                    : session.status === "scaned" || session.status === "scaned_but_redirect" ? "var(--primary)"
                    : session.error ? "var(--danger)" : "var(--ink-80)";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {statusBlock}
      <div style={{
        display: "flex",
        gap: 16,
        padding: 16,
        background: "var(--pearl)",
        border: "1px solid var(--hairline)",
        borderRadius: 12,
        flexWrap: "wrap",
        alignItems: "center",
      }}>
        {!confirmed && session.qr_data_url && (
          <div style={{
            background: "#fff",
            padding: 12,
            borderRadius: 10,
            border: "1px solid var(--hairline)",
            flex: "0 0 auto",
          }}>
            <img
              src={session.qr_data_url}
              alt="WeChat QR"
              width={180} height={180}
              style={{ display: "block", imageRendering: "pixelated" }} />
          </div>
        )}
        {confirmed && (
          <div style={{
            width: 180, height: 180, borderRadius: 10,
            background: "var(--success-bg)",
            display: "flex", alignItems: "center", justifyContent: "center",
            color: "var(--success-fg)", fontSize: 56, fontWeight: 700,
            flex: "0 0 auto",
          }}>✓</div>
        )}
        <div style={{ flex: 1, minWidth: 200, display: "flex", flexDirection: "column", gap: 8 }}>
          <div className="t-body-strong" style={{ color: statusColor }}>
            {session.status_label || session.status}
          </div>
          {!ended && (
            <div className="t-meta" style={{ color: "var(--ink-60)" }}>
              请用微信扫描左侧二维码，并在手机端确认。剩余约 {session.expires_in}s 过期。
            </div>
          )}
          {confirmed && session.credentials && (
            <>
              <div className="t-meta" style={{ color: "var(--success-fg)" }}>
                凭据已写入 <code className="t-mono">.env</code> 与 <code className="t-mono">wechat-account.json</code>。
              </div>
              <KV k="账号 ID" v={session.credentials.account_id} />
              {session.credentials.user_id && <KV k="用户 ID" v={session.credentials.user_id} />}
              <div className="t-meta" style={{ color: "var(--ink-60)", marginTop: 6 }}>
                注意：当前进程需重启 <code className="t-mono">sanshiliu serve</code> 才会真正启动微信 Bot。
              </div>
            </>
          )}
          {session.error && (
            <div className="t-meta" style={{ color: "var(--danger)" }}>
              {session.error}
            </div>
          )}
          <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
            {ended ? (
              <button className="btn btn-secondary" onClick={() => setSession(null)}>关闭</button>
            ) : (
              <button className="btn btn-secondary" onClick={cancel}>取消</button>
            )}
            {ended && !confirmed && (
              <button className="btn btn-primary" onClick={start}>重新生成二维码</button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { Settings });
