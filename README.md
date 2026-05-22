# 三十六贱笑 (Sanshiliu Jianxiao) Agent

> 一个**通用 agent 框架**（协议对齐 Claude Code），默认人设为博主数字分身。
> 换 persona 文件即可变成任何人的数字分身。

- Python 3.13+ · OpenAI 兼容标准子集（chat.completions + streaming + tool_calls）
- 与 Claude Code 文件级互通：CLAUDE.md / memdir 4 类 / SKILL.md / settings.json
- 接入：REPL（已就绪）/ iLink 微信 / Web HTTP

完整开发计划见 `.trellis/tasks/05-21-agent/prd.md`。

---

## 当前进度

| Phase | 主题 | 状态 |
|-------|------|------|
| 1 | 核心引擎（LLM + REPL + storage） | 🟡 进行中 |
| 2 | 三十六贱笑人设 | ⚪ 未开始 |
| 3 | 上下文管理 | ⚪ 未开始 |
| 4 | iLink 微信 + Web HTTP | ⚪ 未开始 |
| 5 | 工具调用 | ⚪ 未开始 |
| 6 | Skills 技能系统 | ⚪ 未开始 |
| 7 | 记忆系统 | ⚪ 未开始 |
| 8 | 安全与权限 | ⚪ 未开始 |
| 9 | 启动入口 & GA | ⚪ 未开始 |

---

## 快速开始（Phase 1 GA 状态）

```powershell
# 1. 创建虚拟环境（Python 3.13+）
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. 安装依赖
pip install -e ".[dev]"

# 3. 配置环境变量
Copy-Item .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL

# 4. 跑通最小 REPL
python -m sanshiliu repl
```

---

## 仓库结构

```
src/sanshiliu/
├── foundation/   # L0 基础：config / logging / errors / retry
├── storage/      # L0 持久化：sqlite + jsonl
├── llm/          # L2 LLM 客户端（async）
├── engine/       # L2 对话循环
├── identity/     # L3 人设层（Phase 2）
├── context/      # L4 上下文管理（Phase 3）
├── memory/       # L5 记忆层（Phase 7）
├── skills/       # L6 技能层（Phase 6）
├── tools/        # L7 工具层（Phase 5）
├── security/     # L8 权限层（Phase 8）
├── channels/     # L9 接入层（REPL/wechat/web）
└── observability/# 健康与指标
```

---

## 开发约定

- `ruff check` + `ruff format` 0 错
- `mypy src/` strict 模式通过
- `pytest` 全绿，关键模块覆盖率 ≥ 70%
- 注释 / 日志中文，仅在关键决策点添加
- 严禁绕过权限或重写 git 历史
