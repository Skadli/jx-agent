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
| `SANSHILIU_GROWTH_ENABLED` | `false` | 成长系统总开关；关=整条成长线停（也是外部 skill 自动安装的 kill-switch）；仅 `serve` 生效 |
| `SANSHILIU_GROWTH_HOUR` | `3` | 成长定时器醒来的小时（0-23，local time） |
| `SANSHILIU_GROWTH_YEARS_PER_CHAPTER` | `5` | 每章成长梦跨多少年 |
| `SANSHILIU_GROWTH_START_AGE` | `5` | 成长起点年龄（原三十六贱笑起点） |
| `SANSHILIU_GROWTH_END_AGE` | `30` | 成长终点年龄；跑满即定格不再推进 |
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

4 类 md（`user_*` / `feedback_*` / `project_*` / `reference_*`）+ `MEMORY.md` 索引。frontmatter 必填 `name` / `description` / `metadata.type`。可选 `metadata.apply: always` 表示正文每轮直接注入 system prompt，适合称呼、语气、格式等必须长期遵守的偏好；未标记的条目只进索引，由 LLM 按需 `LoadMemory`。索引超 200 行自动截断 + WARNING 头。

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

Dashboard 的 Skills 页支持把每个 skill 的 `structure.json` 渲染成 Dify 风格的无限画布：点击行 → 右侧滑出抽屉 → 切到「画布」tab。结构文件放在对应目录的 `skills/<skill-id>/structure.json`，由维护者根据 `SKILL.md` 内容整理。Dashboard 运行时只读取这些结构文件，前端用 `@xyflow/react` UMD 渲染（vendor 在 `dashboard/vendor/`，离线可用）。

**结构文件格式**：

| 节点类型 | 信号来源 | 形状/色带 |
|---------|---------|----------|
| `trigger` | 这个 skill 何时触发 | 圆角矩形 / success |
| `step` | 真实工作流步骤 | 矩形 / primary |
| `tool` | 会调用的工具、命令或外部能力 | 矩形 / warning |
| `subagent` | 会委派的子 agent / grader / analyzer | 双框 / primary-focus |
| `resource` | 需要读取的 `references/` `scripts/` `assets/` 文件 | 矩形 / ink-48 |
| `output` | 最终输出、写入或交付物 | 圆角矩形 / success |

每个 `structure.json` 至少包含：

```json
{
  "nodes": [
    {
      "id": "trigger",
      "type": "custom",
      "position": { "x": 0, "y": 280 },
      "data": {
        "type": "trigger",
        "title": "触发条件",
        "desc": "短描述",
        "raw": "详情"
      }
    }
  ],
  "edges": [
    {
      "id": "e-trigger-step-1",
      "source": "trigger",
      "target": "step-1",
      "type": "custom",
      "data": { "kind": "anchor" }
    }
  ],
  "meta": {
    "structure_version": 2,
    "skill_id": "example",
    "raw_body": "SKILL.md 正文"
  }
}
```

**边规则**：`sequence` 表示主流程，`anchor` 表示 trigger/output 锚点，`tool` / `subagent` / `resource` 表示旁路依赖。Dashboard 不再从 `SKILL.md` 自动推导画布；改动 skill 后需要同步维护对应的 `structure.json`。

---

## 成长系统（growth）

数字分身的"逐章成长"。在调度层注册为一个心跳任务 `growth`（与现有的 `dream` 做梦任务并列），**仅 `serve` 进程生效**（REPL 不跑调度器）。**默认关闭**（`SANSHILIU_GROWTH_ENABLED=false`）。

开启后每天推进一章成长：从 **5 岁起、每章跨 1 年、共 25 章长到 30 岁定格**。每章读前几章传记、逻辑自洽地往后续写本章经历（写得**具体**、可**天马行空**——修仙/穿越/奇遇都行，但要圆得回来），产出三件事：

- **传记**：写入 memdir `reference_growth-chapter-N.md`（永久，作为下一章输入）。
- **人格整体演化**：把核心人格**整盘改写**成"这岁数已经长成的那个人"，版本化存进 `data/growth/persona/chapter-N/`，由 `PersonaLoader` 的 active-core provider 在成长激活时**覆盖** base `persona/core/`（base 文件全程不写、可回滚；切回/回退 `active_persona_chapter` 即换人格）。世界观不隔离——长成校长就是校长人格，日常对话即以长成的人回应。
- **技能习得**：成长系统把本章 `skill_intents`（优先）和 `learned` 合并成安装线索，由代码确定性搜索 Skills.sh / ClawHub 并自动安装真实 skill（按 skills 目录前后 diff 记账，`source=growth-chapter-N`）；**不自造 skill**，找不到当章不装。

### 开启与触发

1. `.env` 设 `SANSHILIU_GROWTH_ENABLED=true`（可选调 `SANSHILIU_GROWTH_HOUR` 等，见上方配置表）。
2. 以 `python -m sanshiliu serve` 启动（REPL 不跑成长）。
3. 调度走心跳：默认每天 `GROWTH_HOUR` 点自动推进一章，或在 dashboard **心跳模块**对 `growth` 任务点"立即运行 / 开关 / 改配置"（即 `/api/heartbeat/growth/*`）手动推进，连点可快速跑满 25 章验证。

> **改每章年数后的迁移**：`growth-state.json` 一旦存在即真相源，会沿用它保存的 `years_per_chapter` / `end_chapter`（改 `.env` 不打乱在跑的成长线）。要把旧的 5 年/章迁到新的 1 年/章：在 dashboard 成长历史点「清空全部」（= 按当前 config 重置 cadence），或直接删掉 `data/growth-state.json` 重建。

### Dashboard 成长模块

`dashboard/views/growth.jsx` 是"看结果"面：时间线（5→30、当前章/岁、进度）、每章传记/汇报/习得 skills/人格快照、**成长历史**（可逐章删除 / 清空）；调度动作（enable / 立即运行 / toggle）复用**心跳模块**。读端点：`GET /api/growth`（总览）、`GET /api/growth/chapters/{n}`（章详情）、`GET /api/growth/persona/{n}`（该章人格快照）。删除：`DELETE /api/growth/chapters/{n}`——删该章及其后所有章（连传记 + 人格快照目录一并清），删第 1 章 = 清空全部并按当前 config 重置 cadence；成长任务正在跑时返回 409。成长未激活时总览优雅返回空闲态，不报错。

### 安全说明（诚实披露）

外部 skill 自动安装在凌晨**无人值守、无人工审批**地发生——这是用户明确知情并接受的供应链/prompt 注入风险。它**有界**：`settings.deny` 命中、PathGuard 黑名单、`critical` 档 bash（`rm -rf` / `dd` / `mkfs` 等）的硬拒绝都在权限状态机里**先于**成长自动放行返回，成长放行只作用于 `defaultMode=ask` 才会询问的那批非 critical 调用（Skill 本身、`git clone` / `npx` 等），且每次放行写一行审计日志、另落 `tool_calls` 表。**全局 kill-switch = `SANSHILIU_GROWTH_ENABLED=false`**：关掉则整条成长线（含自动放行）立即停摆。已装外部 skill 的自动卸载未做（人格可回滚，skill 卸载二期）。

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
