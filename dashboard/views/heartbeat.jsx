/* Heartbeat — 心跳任务面板。
 * 列出所有 HeartbeatTask，可 toggle / run-now / inline 编辑配置。
 * 配置持久化到 <data_dir>/heartbeat.json，重启后保留。
 * 5s 轮询；状态颜色：ok=绿 / gate-failed=灰 / error=红 / running=蓝。
 */

function Heartbeat({ onJump }) {
  const [tasks, setTasks]   = React.useState([]);
  const [expanded, setExpanded] = React.useState({}); // name → bool
  const [busy,  setBusy]    = React.useState({});
  const [msg,   setMsg]     = React.useState("");

  const refresh = React.useCallback(async () => {
    const r = await API.get("/api/heartbeat");
    if (!r.error) setTasks(r.tasks || []);
  }, []);

  React.useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  const flash = (text) => {
    setMsg(text);
    setTimeout(() => setMsg(""), 4000);
  };

  const runNow = async (name) => {
    setBusy(b => ({ ...b, [name]: true }));
    const r = await API.post(`/api/heartbeat/${encodeURIComponent(name)}/run`);
    setBusy(b => ({ ...b, [name]: false }));
    if (r.error) flash(`触发失败：${r.error}`);
    else flash(r.started ? `已触发 ${name}` : `未触发 ${name}：${r.reason}`);
    refresh();
  };

  const toggle = async (name, next) => {
    setBusy(b => ({ ...b, [name]: true }));
    const r = await API.post(`/api/heartbeat/${encodeURIComponent(name)}/toggle`, { enabled: next });
    setBusy(b => ({ ...b, [name]: false }));
    if (r.error) flash(`切换失败：${r.error}`);
    refresh();
  };

  const saveConfig = async (name, patch) => {
    setBusy(b => ({ ...b, [name]: true }));
    const r = await API.put(`/api/heartbeat/${encodeURIComponent(name)}/config`, patch);
    setBusy(b => ({ ...b, [name]: false }));
    if (r.error) flash(`保存失败：${r.error}`);
    else { flash(`已保存 ${name} 配置`); setExpanded(e => ({ ...e, [name]: false })); }
    refresh();
  };

  const enabledCount = tasks.filter(t => t.enabled).length;

  return (
    <div data-screen-label="10 心跳">
      <PageHeader
        title="心跳"
        sub={`${tasks.length} 个任务 · ${enabledCount} 启用 · 配置持久化到 data/heartbeat.json`}
        actions={<button className="btn btn-secondary" onClick={refresh}><Icon name="refresh" size={13}/>刷新</button>}
      />

      <div className="page-body">
        {msg && (
          <div className="card card-padded" style={{ marginBottom: 12, background: "var(--pearl)" }}>
            <span className="t-meta">{msg}</span>
          </div>
        )}

        {tasks.length === 0 ? (
          <div className="card card-padded t-meta" style={{ textAlign: "center", color: "var(--ink-60)" }}>
            暂无注册的心跳任务（heartbeat 只在 <code>serve</code> 模式跑）
          </div>
        ) : (
          <div className="card">
            <table className="tbl">
              <thead>
                <tr>
                  <th style={{ width: 32 }}></th>
                  <th>名称 / 说明</th>
                  <th style={{ width: 140 }}>调度</th>
                  <th style={{ width: 130 }}>状态</th>
                  <th style={{ width: 160 }}>上次 / 下次</th>
                  <th style={{ width: 240 }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {tasks.map(t => (
                  <React.Fragment key={t.name}>
                    <tr>
                      <td>
                        <input
                          type="checkbox"
                          checked={t.enabled}
                          disabled={!!busy[t.name]}
                          onChange={e => toggle(t.name, e.target.checked)}
                        />
                      </td>
                      <td>
                        <div className="t-row-strong">{t.name}</div>
                        <div className="t-meta" style={{ color: "var(--ink-60)", marginTop: 2 }}>
                          {t.description}
                          {t.has_gate && <span className="chip" style={{ marginLeft: 8, fontSize: 10 }}>有 gate</span>}
                        </div>
                      </td>
                      <td className="t-mono-sm">{scheduleLabel(t)}</td>
                      <td>{statusBadge(t)}</td>
                      <td className="t-meta">
                        <div>{t.last_run_at ? `上次 ${API.relTime(t.last_run_at * 1000)}` : "从未运行"}</div>
                        <div style={{ color: "var(--ink-48)" }}>
                          {Number.isFinite(t.next_fire_at) ? `下次 ${API.relTime(t.next_fire_at * 1000)}` : "未调度"}
                        </div>
                      </td>
                      <td>
                        <div style={{ display: "flex", gap: 6 }}>
                          <button className="btn btn-secondary btn-sm" disabled={!!busy[t.name]}
                                  onClick={() => runNow(t.name)}>
                            <Icon name="play" size={11} />立即执行
                          </button>
                          <button className="btn btn-secondary btn-sm"
                                  onClick={() => setExpanded(e => ({ ...e, [t.name]: !e[t.name] }))}>
                            <Icon name="settings" size={11} />{expanded[t.name] ? "收起" : "编辑"}
                          </button>
                        </div>
                      </td>
                    </tr>
                    {expanded[t.name] && (
                      <tr>
                        <td colSpan={6} style={{ background: "var(--canvas)", padding: 16 }}>
                          <ConfigForm task={t} busy={!!busy[t.name]} onSave={patch => saveConfig(t.name, patch)} />
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="t-meta" style={{ marginTop: 12, color: "var(--ink-60)" }}>
          <strong>提示</strong>：开关与配置改完即时落盘 <code>data/heartbeat.json</code>。
          <code>.env</code> 里的 <code>SANSHILIU_DREAM_SCHEDULER_*</code> 仅作首次启动 seed。
        </div>
      </div>
    </div>
  );
}

/* 单个任务的配置编辑表单。 */
function ConfigForm({ task, busy, onSave }) {
  const initialMode = task.daily_at_hour != null ? "daily" : (task.interval_seconds != null ? "interval" : "manual");
  const [mode, setMode]               = React.useState(initialMode);
  const [hour, setHour]               = React.useState(task.daily_at_hour ?? 3);
  const [interval, setInterval_]      = React.useState(task.interval_seconds ?? 3600);
  const [extras, setExtras]           = React.useState({ ...task.extra_params });

  // task 更新时重新同步表单（5s 轮询不要把用户输入冲掉——只在 expand 第一次同步）
  // 简化策略：信任 expand 时拿到的 task 快照；用户保存后行就收起来
  // 这里无 effect 同步是有意的

  const editable = task.editable_params || {};

  const submit = (e) => {
    e.preventDefault();
    const patch = {};
    if (mode === "daily") {
      patch.daily_at_hour = parseInt(hour, 10);
      patch.interval_seconds = null;
    } else if (mode === "interval") {
      patch.daily_at_hour = null;
      patch.interval_seconds = parseInt(interval, 10);
    } else {
      patch.daily_at_hour = null;
      patch.interval_seconds = null;
    }
    const epPatch = {};
    for (const k of Object.keys(editable)) {
      const v = extras[k];
      const spec = editable[k];
      if (spec.type === "int") epPatch[k] = parseInt(v, 10);
      else if (spec.type === "bool") epPatch[k] = !!v;
      else epPatch[k] = String(v ?? "");
    }
    patch.extra_params = epPatch;
    onSave(patch);
  };

  return (
    <form onSubmit={submit} style={{ display: "grid", gap: 12 }}>
      <div>
        <div className="t-row-strong" style={{ marginBottom: 6 }}>调度方式</div>
        <label style={{ marginRight: 16 }}>
          <input type="radio" checked={mode === "daily"} onChange={() => setMode("daily")} /> 每天定时
        </label>
        <label style={{ marginRight: 16 }}>
          <input type="radio" checked={mode === "interval"} onChange={() => setMode("interval")} /> 周期
        </label>
        <label>
          <input type="radio" checked={mode === "manual"} onChange={() => setMode("manual")} /> 仅手动
        </label>
      </div>

      {mode === "daily" && (
        <div>
          <label className="t-meta" style={{ display: "block", marginBottom: 4 }}>触发时刻（本地时间，0-23 时）</label>
          <input className="field" type="number" min={0} max={23} value={hour}
                 onChange={e => setHour(e.target.value)} style={{ width: 100 }} />
        </div>
      )}
      {mode === "interval" && (
        <div>
          <label className="t-meta" style={{ display: "block", marginBottom: 4 }}>触发间隔（秒，>= 1）</label>
          <input className="field" type="number" min={1} value={interval}
                 onChange={e => setInterval_(e.target.value)} style={{ width: 140 }} />
        </div>
      )}

      {Object.keys(editable).length > 0 && (
        <div>
          <div className="t-row-strong" style={{ marginBottom: 6 }}>任务参数</div>
          <div style={{ display: "grid", gap: 8 }}>
            {Object.entries(editable).map(([key, spec]) => (
              <div key={key}>
                <label className="t-meta" style={{ display: "block", marginBottom: 4 }}>
                  {spec.label || key}
                  {spec.hint && <span style={{ color: "var(--ink-48)", marginLeft: 8 }}>{spec.hint}</span>}
                </label>
                {spec.type === "int" ? (
                  <input className="field" type="number" min={spec.min} max={spec.max}
                         value={extras[key] ?? ""}
                         onChange={e => setExtras(s => ({ ...s, [key]: e.target.value }))}
                         style={{ width: 140 }} />
                ) : spec.type === "bool" ? (
                  <input type="checkbox" checked={!!extras[key]}
                         onChange={e => setExtras(s => ({ ...s, [key]: e.target.checked }))} />
                ) : (
                  <input className="field" type="text" value={extras[key] ?? ""}
                         onChange={e => setExtras(s => ({ ...s, [key]: e.target.value }))} />
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={{ display: "flex", gap: 8 }}>
        <button type="submit" className="btn btn-primary btn-sm" disabled={busy}>
          <Icon name="check" size={11} color="#fff" />保存
        </button>
      </div>
    </form>
  );
}

function scheduleLabel(t) {
  if (t.daily_at_hour != null) return `每天 ${String(t.daily_at_hour).padStart(2,"0")}:00`;
  if (t.interval_seconds != null) {
    const s = t.interval_seconds;
    if (s >= 3600) return `每 ${Math.round(s/3600)}h`;
    if (s >= 60)   return `每 ${Math.round(s/60)}min`;
    return `每 ${s}s`;
  }
  return "仅手动";
}

function statusBadge(t) {
  const map = {
    "ok":          { label: "正常",       cls: "chip-success" },
    "running":     { label: "运行中",     cls: "chip-info"    },
    "gate-failed": { label: "闸门未过",   cls: "chip"          },
    "error":       { label: "出错",       cls: "chip-danger"  },
    "never-run":   { label: "未运行",     cls: "chip"          },
  };
  const s = map[t.last_status] || { label: t.last_status, cls: "chip" };
  return (
    <div>
      <span className={`chip ${s.cls}`} style={{ fontSize: 11 }}>{s.label}</span>
      {t.last_message && (
        <div className="t-meta" style={{ color: "var(--ink-48)", marginTop: 2, fontSize: 11 }}>
          {t.last_message}
        </div>
      )}
    </div>
  );
}

Object.assign(window, { Heartbeat });
