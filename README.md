# 三十六贱笑 (Sanshiliu Jianxiao) Agent

一个通用 agent 框架，协议尽量对齐 Claude Code（CLAUDE.md / memdir / SKILL.md / settings.json），默认人设为博主"三十六贱笑"的数字分身。换 persona 文件即可变成任何人的分身。

- **Python 3.13+**，主依赖 7 个（openai / pydantic / pydantic-settings / structlog / httpx / pyyaml / qrcode）
- **LLM 走 OpenAI 兼容标准子集**：chat.completions + streaming + tool_calls（同一份代码可跑 OpenAI / DeepSeek / GLM / 通义 / OneAPI / Ollama）
- **3 个接入通道**：REPL、iLink 微信 Bot、Web HTTP（含 SSE）
- **与 Claude Code 文件级互通**：`~/.claude/` 下的 CLAUDE.md / memdir / SKILL.md / settings.json 软链或拷过来直接生效

完整开发计划见 [.trellis/tasks/05-21-agent/prd.md](../.trellis/tasks/05-21-agent/prd.md)。

---

## 当前状态（2026-05-23）

Phase 1-9 代码骨架全部落地。恢复记录里曾有 **214 个单元测试通过** 和 6 份 smoke（phase 2-9）通过；当前恢复工作区未包含 `tests/` 目录，commit 前以 targeted check + 手工 smoke 为准。**尚未达 GA**——见文末"已知缺口"。

| Phase | 主题 | 代码 | 单测 | Smoke |
|-------|------|------|------|-------|
| 1 | 核心引擎（LLM + REPL + storage） | ✅ | ⚠️ llm/client 18% | ❌ 无 |
| 2 | 三十六贱笑人设 | ✅ | ✅ | ✅ |
| 3 | 上下文（compact / microcompact / budget） | ✅ | ✅ | ✅ |
| 4 | iLink 微信 + Web HTTP | ✅ | ⚠️ web/wechat <30% | ✅ |
| 5 | 工具调用（web_search / file_io / bash） | ✅ | ⚠️ web_search 27% | ✅ |
| 6 | Skills（SKILL.md） | ✅ | ✅ | ✅ |
| 7 | 记忆（CLAUDE.md + memdir + extract） | ✅ | ✅ | ✅ |
| 8 | 安全权限（settings.json） | ✅ | ✅ | ✅ |
| 9 | 启动入口 + GA 装配 | ✅ | ✅ | ✅ |

---

## 快速开始

### 1. 装依赖

直接使用 Python 自带 `venv` + `pip` 即可。

```powershell
# Windows PowerShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

```bash
# POSIX
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

### 2. 自检环境

```powershell
python -m sanshiliu doctor
```

打印 Python 版本、虚拟环境状态和核心依赖检测。缺什么会直接告诉你用 `pip` 装什么。

### 3. 填配置

```powershell
Copy-Item .env.example .env
# 编辑 .env，按需填写模型配置
python -m sanshiliu setup    # 可选：检测现有配置，并真调一次 LLM 测连通
```

当前程序实际读取进程环境变量和项目根目录 `.env`。Windows 下建议直接编辑项目根目录 `.env`。`setup` 只会询问模型名，不会询问或写入 LLM API key / base URL；如果没有 WeChat channel 凭据，会按 Hermes 的 iLink Bot 流程显示微信二维码，扫码确认后自动保存 `data/wechat-account.json` 并把 `WEIXIN_*` / `ILINK_*` 运行时配置写回项目 `.env`，下次启动自动复用，有新 token 时覆盖旧值。

支持的 backend：

| Backend | base_url | 推荐 model | 国内可达 |
|---------|----------|-----------|---------|
| **DeepSeek（默认）** | `https://api.deepseek.com` | `deepseek-chat` | ✓ |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` | ✓ |
| 阿里 通义 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` | ✓ |
| OneAPI / OpenRouter | 自填 | 自填 | 看后端 |
| Ollama 本地 | `http://localhost:11434/v1` | 视模型 | ✓ |
| OpenAI 官方 | `https://api.openai.com/v1` | `gpt-4o-mini` | 需翻墙 |

### 4. 跑起来

```powershell
python -m sanshiliu repl    # 默认：终端 REPL
python -m sanshiliu serve   # HTTP server（/chat SSE + /healthz + /metrics）
python -m sanshiliu bot     # serve 的别名，强调拉 wechat bot；setup 扫码后会写入 WeChat 凭据
```

REPL 内置命令：`/quit /stats /persona /memory /help`。

---

## 命令行接口

```text
python -m sanshiliu [--version] <command>

<command>:
  repl       交互式对话（默认）；首次运行自动跑 setup 向导
  serve      HTTP 服务（含 SSE）+ 按 .env 决定是否拉 wechat bot
  bot        serve 的别名
  doctor     环境检查（preflight + 依赖检测），不进 REPL
  setup      配置检查向导（检测 .env + 测 LLM 连通）
```

如果已经激活 `.venv` 且 console script 在 PATH 中，也可以直接用 `sanshiliu <command>`。

退出码：`0` 成功 / `78` 配置错误 / `130` 用户中断。

---

## 配置文件

### `.env`（环境变量）

按 `.env.example` 复制后改。优先级：进程 env > 当前目录 `.env`。关键键：

| 键 | 默认 | 说明 |
|---|---|---|
| `OPENAI_API_KEY` | — | **必填**；缺则启动失败 |
| `OPENAI_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容后端；默认走 DeepSeek 国内可达 |
| `OPENAI_MODEL` | `deepseek-chat` | 模型 ID |
| `SANSHILIU_WEB_SEARCH_PROVIDER` | `auto` | 国内强制走 `sogou` 最稳；`auto` 会 Tavily→Sogou→DDG 链式 fallback |
| `SANSHILIU_DATA_DIR` | `./data` | sqlite / 日志 / jsonl 落盘 |
| `SANSHILIU_HOME_DIR` | `~/.sanshiliu` | 用户级目录（CLAUDE.md / memdir / settings.json） |
| `SANSHILIU_PERSONA_DIR` | `./persona` | 人设 md 目录 |
| `SANSHILIU_PROMPTS_DIR` | `./prompts` | 系统 prompts（compact / tools / memory_extract） |
| `SANSHILIU_MAX_CONTEXT_TOKENS` | `128000` | 上下文上限；命中 80% 触发 compact |
| `SANSHILIU_TOOLS_ENABLED` | `true` | 关掉走 Phase 4 行为 |
| `SANSHILIU_SKILLS_ENABLED` | `true` | 关掉不加载 SKILL.md |
| `SANSHILIU_MEMORY_ENABLED` | `true` | 关掉不加载 CLAUDE.md/memdir |
| `SANSHILIU_SECURITY_ENABLED` | `true` | 关掉不审批工具调用 |
| `SANSHILIU_AUTO_EXTRACT_ENABLED` | `false` | 每轮异步提取候选记忆（开了会多调一次 LLM） |
| `SANSHILIU_WECHAT_ENABLED` | `false` | 拉 iLink wechat bot |
| `SANSHILIU_WEB_PORT` | `9527` | HTTP 端口 |
| `WEIXIN_ACCOUNT_ID` / `WEIXIN_TOKEN` | — | Hermes 风格官方 iLink Bot 凭据；setup 扫码后自动写入 |
| `WEIXIN_BASE_URL` | `https://ilinkai.weixin.qq.com` | 官方 iLink Bot API 地址 |
| `WEIXIN_ACCOUNT_STORE` | `data/wechat-account.json` | 扫码账号缓存；后续启动复用 |
| `WEIXIN_QR_FILE` | `data/wechat-login-qr.svg` | 终端二维码识别失败时的备用 SVG |
| `WEIXIN_QR_LOGIN` | `true` | 无微信凭据时是否在 setup 中拉二维码；设为 `false` 可跳过 |
| `WEIXIN_QR_OPEN_FILE` | `true` | 终端无法安全渲染二维码时，尝试自动打开 SVG |
| `SANSHILIU_WECHAT_WHITELIST` | — | 逗号分隔 wxid；空集合一律拒绝，调试可填 `*` |
| `ILINK_API_KEY` / `ILINK_WEBHOOK_SECRET` | — | 旧本地 iLink webhook 兼容模式凭据 |

### `settings.json`（权限）

放当前目录或 `~/.sanshiliu/`（项目级覆盖全局级）。schema 完全照搬 Claude：

```json
{
  "permissions": {
    "defaultMode": "ask",
    "allow": ["Bash(ls:*)", "Bash(git status)", "Read(./**)", "WebSearch"],
    "deny":  ["Bash(rm:-rf*)", "Read(~/.ssh/**)", "Write(/etc/**)"]
  }
}
```

模式语法：

- `Bash(verb:arg_glob)`：`bash_exec.command` 首词等于 `verb` 且剩余部分匹配 glob
- `Bash(verb)` / `Bash(exact command)`：精确匹配
- `Read(<glob>)` / `Write(<glob>)`：`path` 参数命中 glob
- `WebSearch`：纯工具名匹配

defaultMode：`allow` / `deny` / `ask`（ask 在 REPL 弹确认；wechat/web 通道默认拒绝）。用户选"always"会自动追加到 `permissions.allow` 持久化。

### `persona/`（人设）

分两层加载：

- `persona/core/`：全量常驻进 system prompt 的核心人格，**按字母序拼接**。默认 5 份 md（建议总长 ≤ 2k tokens）：
  - `identity.md` — 我是谁 / 背景 / 红线
  - `style.md` — 说话风格硬约束 + anti-pattern + `<MSG>` 拆分规则
  - `personality.md` — 性格八维 + OCEAN
  - `beliefs.md` — 价值观底线与红线
  - `fewshot_short.md` — 短样本（微信节奏 ≤ 30 字）
- `persona/modules/`：按需注入的扩展模块，每份 md 含 frontmatter（`name` / `description` / `trigger_keywords`）。默认 8 份：
  - 作品：`works_dubbing.md`（配音短剧）/ `works_vlog.md`（真人 Vlog）
  - 知识：`knowledge_timeline.md`（公开数据 / 平台 / 时间线）/ `advisor_methodology.md`（5 心智模型 + 8 启发式）
  - 长样本：`fewshot_advisor.md`（创作顾问）/ `fewshot_emotion.md`（情绪接住）/ `fewshot_roleplay.md`（配音剧扮演）
  - 风格补充：`style_phrases.md`（控场口头禅扩展）

加载策略：core/ 全量常驻；modules/ 由引擎按 user_text 命中 `trigger_keywords` 注入 0-1 个，或由 LLM 主动调 `LoadPersonaModule` 工具按 `name` 拉取。换 persona 直接改 md 即可，watcher 5s 轮询 mtime 自动 reload。

### `memdir/`（长期记忆）

4 类 md（`user_*` / `feedback_*` / `project_*` / `reference_*`）+ `MEMORY.md` 索引。frontmatter 必填 `name` / `description` / `metadata.type`。索引超 200 行自动截断 + WARNING 头。

### `skills/<skill-id>/SKILL.md`

frontmatter 含 `name` / `description` / `keywords`。3 个目录扫描：项目级 `./.sanshiliu/skills/` > 仓库内 `./skills/` > 全局 `~/.sanshiliu/skills/`。用户消息命中 `keywords` 时，SKILL body 注入 system prompt 末尾 `<active_skills>` 段。

---

## 与 Claude Code 协议互通

把 Claude 的目录软链或拷到 `~/.sanshiliu/` 直接生效：

```bash
ln -s ~/.claude/CLAUDE.md   ~/.sanshiliu/CLAUDE.md
ln -s ~/.claude/memdir      ~/.sanshiliu/memdir
ln -s ~/.claude/skills      ~/.sanshiliu/skills
cp    ~/.claude/settings.json ~/.sanshiliu/settings.json
```

权限模式名映射：运行时 `bash_exec` ↔ 协议名 `Bash`，`file_read` ↔ `Read`，`file_write` ↔ `Write`，`web_search` ↔ `WebSearch`。Claude 写的 `Bash(ls:*)` 在本项目里同样匹配 `bash_exec`。

---

## Skill 可视化画布

Dashboard 的 Skills 页支持把每个 SKILL.md 渲染成 Dify 风格的无限画布：点击行 → 右侧滑出抽屉 → 切到「画布」tab。后端 `parse_skill_structure`（[`src/sanshiliu/skills/structure.py`](src/sanshiliu/skills/structure.py)）用启发式把 markdown 抽成 5 类节点 + 6 类边，前端用 `@xyflow/react` UMD 渲染（vendor 在 `dashboard/vendor/`，离线可用）。

**节点抽取启发式**（写 SKILL.md 时遵循即可得到合理画布）：

| 节点类型 | 信号来源 | 形状/色带 |
|---------|---------|----------|
| `trigger` | frontmatter `keywords` > `## When to Use` > `## Activation Signals` | 圆角矩形 / success |
| `step` | `## Workflow` / `## Steps` 下的**有序列表**或 `### 第 N 步` / `### Step N` 风格 H3 | 矩形 / primary |
| `tool` | 行内 tool 名（bash_exec/Read/Skill/...）；frontmatter `allowed-tools` | 矩形 / warning |
| `subagent` | 引用 `agents/X.md` 或正文出现 spawn/delegate | 双框 / primary-focus |
| `resource` | 引用 `references/` `scripts/` `assets/` 或 `~/path` | 矩形 / ink-48 |
| `output` | `## Output` / `## Recommendation Format` / `## Report structure` | 圆角矩形 / success |

**边规则**：相邻 step 顺序边、tool 短挂、subagent 虚线、resource 灰双向、trigger→首 step、末 step→output。step 超过 5 自动蛇形换行。

**写 skill 让画布漂亮**：

1. 在主流程段加 `## Workflow` 或 `## Steps`，下面用编号列表（`1. ...`）或 `### 第 N 步` 写每一步
2. 在 frontmatter 写 `allowed-tools: [Read, Write, Skill]` 或正文里行内提及工具名
3. 加 `## Output` / `## Recommendation Format` 段写明输出格式
4. 不规范的 SKILL.md 会显示 warning 提示而非自动假造结构

详细规则见 `.trellis/tasks/05-28-skill-dify/research/skill-viz-patterns.md`。

---

## 仓库结构

```
jx-agent/
├── pyproject.toml             # 主依赖 7 个；ruff/mypy/pytest 配置
├── .env.example
├── settings.json.example      # Claude 风格权限示例
├── CLAUDE.md                  # 项目级长期记忆（启动注入 system prompt 顶部）
│
├── persona/                   # L3 人设；core/ 全量常驻 + modules/ 按需注入
│   ├── core/                  # 常驻 system prompt，按字母序拼接
│   │   ├── identity.md        # 我是谁 / 背景 / 红线
│   │   ├── style.md           # 说话风格硬约束 + anti-pattern + <MSG> 规则
│   │   ├── personality.md     # 性格八维 + OCEAN
│   │   ├── beliefs.md         # 价值观底线 / 红线
│   │   └── fewshot_short.md   # 短样本（微信节奏 ≤ 30 字）
│   └── modules/               # 按需注入；frontmatter 含 name/description/trigger_keywords
│       ├── works_dubbing.md   # 配音短剧节目知识
│       ├── works_vlog.md      # 真人 Vlog/整蛊节目知识
│       ├── knowledge_timeline.md   # 公开数据 / 平台 / 时间线
│       ├── advisor_methodology.md  # 5 心智模型 + 8 决策启发式
│       ├── fewshot_advisor.md      # 创作顾问长样本
│       ├── fewshot_emotion.md      # 情绪接住样本
│       ├── fewshot_roleplay.md     # 配音剧扮演样本
│       └── style_phrases.md        # 控场口头禅扩展
│
├── memdir/                    # L5 长期记忆；MEMORY.md 索引 + 4 类 md
├── skills/                    # L6 仓库内自带 SKILL.md
├── prompts/                   # 系统 prompts（compact / microcompact / memory_extract）
│   └── tools/                 # 工具描述 md（frontmatter 含 name/description/parameters）
│
├── src/sanshiliu/
│   ├── __init__.py            # __version__ = "1.0.0"
│   ├── __main__.py            # python -m sanshiliu
│   ├── cli.py                 # argparse 入口；repl/serve/bot/doctor/setup
│   │
│   ├── foundation/            # L0：config / logging / errors / retry / frontmatter
│   ├── storage/               # L0：sqlite DAO + jsonl writer + schema.sql
│   │
│   ├── llm/                   # L2：openai AsyncOpenAI 封装 + 流式 + cost 记账
│   ├── engine/                # L2：对话循环 + session + prompt_builder + tool_call 循环
│   │
│   ├── identity/              # L3：persona loader + 5s watcher（mtime 轮询）
│   ├── context/               # L4：history + compact + microcompact + budget
│   ├── memory/                # L5：CLAUDE.md + memdir + wiki-link + 异步 extract
│   ├── skills/                # L6：SKILL.md 加载 + matcher + activator
│   ├── tools/                 # L7：dispatcher + registry + builtin（web_search/file_io/bash）
│   ├── security/              # L8：settings.json + permission 状态机 + bash classifier + path guard + ReplConfirmer
│   │
│   ├── channels/              # L9：REPL / wechat (iLink + webhook + queue + 黑名单) / web (server + SSE + routes)
│   ├── bootstrap/             # L1：preflight + install + setup_wizard + banner + wire(App)
│   └── observability/         # healthz / metrics（含在 web/handlers 里）
│
├── data/                      # 运行时（gitignore）：sqlite / logs / jsonl / htmlcov
└── tests/                     # 当前恢复工作区未包含；恢复后放单测和 smoke
```

### 分层依赖图

```
                       L1 启动层 (Bootstrap → App)
                                  │
        ┌─────────────────────────┼──────────────────────────┐
        ▼                         ▼                          ▼
     L9 接入层               L2 核心引擎                 L8 安全权限层
     (REPL/iLink/HTTP)       (LLM + 对话循环)            (settings/state-machine)
        │                         │
        └────────────┬────────────┘
                     ▼
              L4 上下文管理层 (history + compact + budget)
                     │
   ┌────────┬────────┼────────┬────────┐
   ▼        ▼        ▼        ▼        ▼
 L3 身份  L5 记忆  L6 技能  L7 工具  L0 基础设施
```

---

## 开发约定

- `ruff check` + `ruff format` 零错；中文项目允许全角标点（已在 `pyproject.toml` 中 ignore RUF001/2/3）
- `mypy src/sanshiliu` strict 模式：历史记录里仍有 26 个错待修（见缺口清单）
- 当前恢复工作区未包含 `tests/`，先跑 targeted `ruff` / `py_compile` / 手工 smoke
- 若恢复 `tests/`，再跑 `pytest tests/unit -q` 和 `python -m tests.smoke.smoke_phase<N>`
- 注释 / 日志中文，仅在关键决策点添加；不写 what，只写 why
- 严禁绕过权限或重写 git 历史

### 测试入口

```powershell
python -m ruff check src/sanshiliu
python -m py_compile src/sanshiliu/bootstrap/setup_wizard.py
python -m sanshiliu doctor
```

---

## 已知缺口（GA 前必修）

子代理审计（2026-05-23）核出主要待修项：

**P0 阻塞 GA**

1. `llm/client.py` 覆盖率 **18%**（要求 ≥75%）——需补 mock httpx 的重试 / stream / 错误映射单测
2. `llm/cost.py` 22%、`foundation/retry.py` 47%——同上
3. `engine/loop.py` 覆盖率 **46%**——tool_call 循环 + dedupe + budget 反查路径缺单测
4. `skills/matcher.py` 的**语义匹配是 stub**（仅 keyword 路径，embedding 未接）——6-V3 未达
5. `tests/smoke/smoke_phase1.py` **缺失**
6. **9 个 Phase tag 全部未打**，更没 `v1.0.0`

**P1 安全 / 工程**

7. `bash_exec` 走 shell 拼接执行 LLM 字符串；classifier 仅正则可被混淆绕过——critical 档应硬拒
8. `PermissionManager._session_cache` 无锁；多通道并发可能双弹确认 / 双写 settings.json
9. iLink webhook HMAC **无 timestamp/重放保护**——建议加 5min 时间窗
10. `channels/web/handlers.py` 中 `healthz` 的 wechat 状态硬编码为 disabled（4-V7 不真实）
11. `mypy --strict` 26 错；`tools/registry.py` 4 处用 `list` 方法名当类型注解（**High**）；`bootstrap/install.py:88` "object" not callable
12. `ruff check` 37 错（25 个可一键 `--fix`）

**P2 运营 / 文档**

13. SHIP checklist 后 6 条（14 天封号观察、≥500 真实对话、≤¥150 成本、双 backend 实跑、Claude skill 兼容性演示）均需运行期数据，目前无证据

**当前结论**：可打 `v1.0.0-rc1`；待 P0/P1 收口 + 14 天运行验证后再 `v1.0.0`。

---

## License

Proprietary（暂不公开）。
