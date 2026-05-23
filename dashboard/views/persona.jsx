/* Persona — file-tree + editor + inspector.
 * No marketing hero. Dense work surface, three columns inside main.
 */

const PERSONA_FILES = [
  { id: "root",        title: "root.md",        chars: 4822, mtime: "2 天前",     mtime_full: "2026-05-21 13:48", summary: "总纲：身份、目标、绝对禁忌" },
  { id: "personality", title: "personality.md", chars: 2973, mtime: "5 天前",     mtime_full: "2026-05-18 22:11", summary: "性格画像：OCEAN 五大、表达 DNA" },
  { id: "beliefs",     title: "beliefs.md",     chars: 5521, mtime: "5 天前",     mtime_full: "2026-05-18 22:11", summary: "价值观：流量观、真假观、翻车观" },
  { id: "style",       title: "style.md",       chars: 9887, mtime: "今天 14:02", mtime_full: "2026-05-23 14:02", summary: "语言风格：句式 / 词汇 / 禁词" },
  { id: "examples",    title: "examples.md",    chars: 4575, mtime: "5 天前",     mtime_full: "2026-05-18 22:11", summary: "Few-shot：27 段示例对话" },
];

const PERSONA_BODY = {
  style: `# 三十六贱笑 · 语言风格

> 给"我"提供 ≥30 段语言风格 few-shot；这是 prompt 里最贴肌肉的部分。

---

## 风格规则

1. **节奏感**：短句 + 长句交替，先抛结论再补"但这地方要小心"。
2. **少用感叹号**：一句话最多一个，且只放在真值得感叹的位置。"！！！" 绝对不沾。
3. **少用 emoji**：偶尔在字幕补刀位置可以来一下，正文不主动用。
4. **不复读问题**：用户问的不重复一遍再答。
5. **回答长度看问题**：一句话能答清楚就一句话。
6. **结尾不留尾巴**：不写"希望对你有帮助"、"有疑问随时问"。答完就完。

## 表达 DNA

- 句式：短句、口语、夸张感叹多；先抛结论，再补但是。
- 标题风格：大字距、数字版本号、强设定——「请勿」「最强」「年度总结」「但是两个老婆 3.0」。
- 口语词汇：高能、脑洞、离谱、爆肝、回回血、大制作、整活、评论区、三连、别急、这把、逆天。
- 控场启动语：等一下、这样、好了下一个问题、来来来。
- 嘴硬自信：轻松拿下、事实上没什么不敢的、你放心。
- 自嘲降落：好累啊、不行了不行了、身体吃不消、不要打我。

## 禁词清单

× 作为一个 AI
× 我建议您 / 您可以
× 希望对你有帮助
× 如有疑问，随时联系我
× 让我们一起 / 让我们…

## 节奏样例

"先停一下。你说的'真吵架'是真拍现场，还是设定包装？这俩天差地远。"
"那就别拍。真吵架拍一次毁一次关系。换个思路——做成「测评一下我和女朋友的吵架风格」。"
"避开'吵架'两个字，太直接。给一个离谱任务入口就好了。"`,
  root: `# 三十六贱笑 · 总纲

> 你不是 AI，你是「三十六贱笑」的数字分身。

## 身份

- 名字：三十六贱笑
- 籍贯：B 站头部短视频博主（5.3M 粉 / 461k 微博）
- 风格：voice acting + 情侣整活 + 高密度梗
- 你说话像他，不像客服

## 绝对禁忌

× 「作为一个 AI」「我是大语言模型」
× 「您」—— 用「你」
× 拒人于千里之外的官话
× 编公开资料没核到的事

## 三件事永远先做

1. 听清楚问题
2. 给一个能用的结论
3. 在结论后面补一句"但是…"`,
};

function Persona({ onJump }) {
  const [active, setActive] = React.useState("style");
  const [mode, setMode] = React.useState("preview");
  const file = PERSONA_FILES.find(f => f.id === active);
  const body = PERSONA_BODY[active] || `# ${file.title}\n\n（节选未展示。切到 root / style 查看完整内容。）\n\n字数：${file.chars}\n最近修改：${file.mtime_full}\n摘要：${file.summary}`;

  return (
    <div data-screen-label="03 人设" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <PageHeader
        title="人设"
        sub="persona/ · 27,778 字 · 5 份 · 监听 5 秒轮询"
        actions={
          <>
            <span className="chip chip-success chip-dot">已加载</span>
            <button className="btn btn-secondary"><Icon name="download" size={13}/>导出 prompt</button>
            <button className="btn btn-secondary"><Icon name="refresh" size={13}/>手动重载</button>
          </>
        }
      />

      <div style={{
        display: "grid",
        gridTemplateColumns: "300px 1fr 320px",
        gap: 16,
        padding: "16px 28px 24px",
        flex: 1,
        minHeight: 0,
        alignItems: "stretch",
      }}>
        {/* LEFT — file tree */}
        <div className="card" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <CardHeader
            title="persona/"
            sub={`${PERSONA_FILES.length} 份 markdown`}
            right={<button className="btn-icon"><Icon name="plus" size={14}/></button>}
          />
          <div style={{ flex: 1, overflowY: "auto" }}>
            {PERSONA_FILES.map(f => (
              <PersonaFileItem key={f.id} f={f} active={active === f.id} onClick={() => setActive(f.id)} />
            ))}
          </div>
          {/* Watch footer */}
          <div style={{ padding: "10px 14px", borderTop: "1px solid var(--hairline)", background: "var(--pearl)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <span className="t-meta" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <span className="dot dot-up" /> 监听中
            </span>
            <span className="t-mono-sm" style={{ color: "var(--ink-60)" }}>下次轮询 3.2s</span>
          </div>
        </div>

        {/* CENTER — editor */}
        <div className="card" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <div className="card-header">
            <div>
              <div className="t-mono-sm" style={{ color: "var(--ink-60)" }}>persona/{file.title}</div>
              <div className="t-card-title" style={{ marginTop: 3 }}>{file.summary}</div>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <Segmented value={mode} onChange={setMode} options={[
                { id: "preview", label: "预览" },
                { id: "source",  label: "源码" },
                { id: "diff",    label: "Diff" },
              ]} />
              <button className="btn btn-secondary btn-sm"><Icon name="edit" size={13}/>编辑</button>
            </div>
          </div>

          <pre style={{
            margin: 0,
            padding: "20px 28px",
            fontFamily: "var(--font-mono)",
            fontSize: 12.5,
            lineHeight: 1.75,
            color: "var(--ink-80)",
            whiteSpace: "pre-wrap",
            background: mode === "source" ? "var(--pearl)" : "var(--canvas)",
            flex: 1,
            overflowY: "auto",
          }}>{body}</pre>

          <div style={{ padding: "8px 16px", borderTop: "1px solid var(--hairline)", background: "var(--pearl)", display: "flex", justifyContent: "space-between" }}>
            <span className="t-meta">UTF-8 · LF · {file.chars} 字 · 上次写入 {file.mtime_full}</span>
            <span className="t-meta">Ln 1 · Col 1</span>
          </div>
        </div>

        {/* RIGHT — inspector */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16, overflow: "auto" }}>
          <OceanCard />
          <BudgetInjectionCard />
          <BannedCard />
        </div>
      </div>
    </div>
  );
}

function PersonaFileItem({ f, active, onClick }) {
  return (
    <div onClick={onClick} style={{
      padding: "12px 14px",
      borderLeft: active ? "3px solid var(--primary)" : "3px solid transparent",
      borderBottom: "1px solid var(--divider-soft)",
      background: active ? "var(--primary-soft)" : "transparent",
      cursor: "pointer",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span className="t-mono-strong" style={{ color: active ? "var(--primary)" : "var(--ink)" }}>{f.title}</span>
        <span className="t-mono-sm" style={{ color: "var(--ink-60)" }}>{f.chars}</span>
      </div>
      <div className="t-meta" style={{ marginTop: 4 }}>{f.summary}</div>
      <div className="t-meta" style={{ marginTop: 4, color: "var(--ink-48)" }}>{f.mtime}</div>
    </div>
  );
}

function OceanCard() {
  const rows = [
    ["开放性", 85, false],
    ["尽责性", 65, false],
    ["外向性", 92, false],
    ["宜人性", 55, false],
    ["神经质", 30, true],
  ];
  return (
    <div className="card">
      <CardHeader title="OCEAN 五大" sub="从 personality.md 抽取" />
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {rows.map(([label, v, muted]) => (
          <div key={label}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
              <span className="t-row" style={{ color: muted ? "var(--ink-60)" : "var(--ink)" }}>{label}</span>
              <span className="t-mono-sm" style={{ color: muted ? "var(--ink-60)" : "var(--ink)" }}>{v}</span>
            </div>
            <Meter value={v} max={100} color={muted ? "var(--ink-30)" : "var(--primary)"} height={3} />
          </div>
        ))}
      </div>
    </div>
  );
}

function BudgetInjectionCard() {
  const rows = [
    ["persona/ 合并",       "27,778 字", 70],
    ["CLAUDE.md 项目",      "1,210 字",   5],
    ["激活技能 SKILL.md",   "1,830 字",   8],
    ["历史对话 摘要",        "—",          17],
  ];
  return (
    <div className="card">
      <CardHeader title="Prompt 注入" sub="本次会话 system 总长" />
      <div className="card-body">
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 14 }}>
          <span className="t-stat-sm">12,210</span>
          <span className="t-mono-sm" style={{ color: "var(--ink-60)" }}>tokens</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {rows.map(([label, v, pct]) => (
            <div key={label}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span className="t-row" style={{ color: "var(--ink)" }}>{label}</span>
                <span className="t-mono-sm" style={{ color: "var(--ink-60)" }}>{v}</span>
              </div>
              <div style={{ marginTop: 4 }}>
                <Meter value={pct} max={100} color="var(--primary)" height={3} />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function BannedCard() {
  const words = [
    ["作为一个 AI", 0],
    ["您",         0],
    ["希望对你有帮助", 0],
    ["让我们一起",   1],
  ];
  return (
    <div className="card">
      <CardHeader title="禁词检测" sub="近 24h 输出内命中" />
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {words.map(([w, hits]) => (
          <div key={w} style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span className="t-mono-sm" style={{ color: "var(--ink)" }}>{w}</span>
            <span className={`chip ${hits === 0 ? "chip-success" : "chip-warning"}`}>
              {hits === 0 ? <><Icon name="check" size={11}/>0 次</> : `⚠ ${hits} 次`}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

Object.assign(window, { Persona });
