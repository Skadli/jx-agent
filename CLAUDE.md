# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

三十六贱笑 (sanshiliu-jianxiao) is a Python 3.13+ agent framework whose file-level protocol is **deliberately aligned with Claude Code**: `CLAUDE.md`, `memdir/`, `skills/<id>/SKILL.md`, and `settings.json` from `~/.claude/` can be symlinked or copied into `~/.sanshiliu/` and just work. The default persona is the blogger "三十六贱笑"; swapping the markdown files in `persona/core/` (+ optionally `persona/modules/`) turns it into anyone's digital twin.

LLM calls use the **OpenAI-compatible standard subset** (chat.completions + streaming + tool_calls), so one codebase runs against OpenAI / DeepSeek / GLM / 通义 / OneAPI / Ollama by changing `OPENAI_BASE_URL`.

Three channels: REPL, iLink WeChat bot, Web HTTP (with SSE).

## Common commands

```powershell
# Setup (Windows PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

# Run
python -m sanshiliu repl        # REPL (default; runs setup wizard on first launch)
python -m sanshiliu serve       # HTTP server (/chat SSE + /healthz + /metrics)
python -m sanshiliu bot         # alias of serve, emphasizes wechat bot
python -m sanshiliu doctor      # preflight + dependency check, no REPL
python -m sanshiliu setup       # config wizard; scans .env, tests LLM connectivity, scans QR for wechat
```

REPL slash commands: `/quit /stats /persona /memory /help`

Exit codes: `0` success, `78` config error, `130` user interrupt.

## Lint / type / test

```powershell
python -m ruff check src/sanshiliu        # ruff (RUF001/2/3 ignored — full-width punctuation OK)
python -m ruff format src/sanshiliu
python -m mypy src/sanshiliu              # strict mode; 0 errors (baseline 清零，禁止再漂)
python -m py_compile src/sanshiliu/<path>.py   # quick syntax check before commit
```

**Note:** the `tests/` directory is **not present in the current restored workspace**. History records 214 passing unit tests and 6 phase smoke scripts, but they cannot be run here. Until tests are restored, verify changes with targeted `ruff` + `py_compile` + manual smoke (`python -m sanshiliu doctor`, then exercise the affected path). If you restore `tests/`, the entrypoints are `pytest tests/unit -q` and `python -m tests.smoke.smoke_phase<N>`.

`pytest` is configured to write coverage HTML to `data/htmlcov/` and requires `--cov=sanshiliu` (set in `pyproject.toml addopts`).

## Architecture: layered (L0–L9)

The codebase enforces a strict layer order. **Higher layers depend on lower; never the reverse.** `src/sanshiliu/bootstrap/wire.py` is the single place that assembles everything into an `App` facade — read it first when tracing how a request flows end-to-end.

```
L1 Bootstrap (wire.App) — orchestrator
├── L9 Channels       repl / web (HTTP+SSE) / wechat (iLink poller+webhook+queue+rate-limit)
├── L2 Engine         LLM client + ConversationEngine.tool_call loop + Session
├── L8 Security       settings.json loader + PermissionManager state machine + bash classifier + PathGuard + Confirmer
├── L4 Context        history + compact + microcompact + budget
│   ├── L3 Identity   persona loader (core/*.md, 全量常驻) + module loader/activator (modules/*.md, 按需注入) + 5s mtime watcher
│   ├── L5 Memory     CLAUDE.md (shortterm pin) + memdir/* (longterm, wiki [[link]]) + async extract
│   ├── L6 Skills     SKILL.md loader + keyword matcher + activator (injects body into system prompt)
│   ├── L7 Tools      registry + dispatcher + builtins (web_search / file_io / bash_exec)
│   └── L0 Foundation config (pydantic-settings) + logging (structlog) + errors + retry + frontmatter
└── L0 Storage        sqlite DAO (asyncio.to_thread wrap stdlib sqlite3) + jsonl writer + schema.sql
```

Where to look for a given concern:
- **request flow**: `engine/loop.py` `ConversationEngine` — runs the tool-call loop with dedupe (threshold 4 for repeat `(name, args)`)
- **system prompt assembly**: `engine/session.py:_effective_system` — order is `memory_block` → `core_persona` (messages[0]) → `persona_modules_listing` → `active_module_text` → `active_skills_text` → `compact_summary`. core 全量常驻；module listing 常驻；module 正文按引擎关键词预判或 `LoadPersonaModule` 工具调用注入
- **adding a tool**: drop a builtin module under `tools/builtin/`, register in `tools/bootstrap.build_tool_stack`, write a description md in `prompts/tools/<name>.md` (frontmatter has `name` / `description` / `parameters`)
- **permission decisions**: `security/permission.py` `PermissionManager` — pattern syntax matches Claude exactly (`Bash(ls:*)`, `Read(./**)`, `WebSearch`)
- **persona swap**:
  - 改人格底色 / 风格 / 短样本：编辑 `persona/core/*.md`（任意 .md，按字母序拼接，全量常驻；总长建议 ≤ 2k tokens）
  - 加 / 改"作品库 / 长样本 / 方法论"等按需知识：放 `persona/modules/*.md`，frontmatter 含 `name` / `description` / `trigger_keywords`；引擎按 user_text 关键词命中 0-1 个注入正文
  - watcher 5s 内自动 reload 两个目录
  - 多消息拆分：LLM 在输出里插 `<MSG>` 让 channel 层切多条独立消息（无延迟、代码块内失效），规则写在 `persona/core/style.md`
- **adding persona module**: 写 `persona/modules/<name>.md` 带 frontmatter，重启或 5s 后生效；LLM 在常驻 listing 段可见，也可调 `LoadPersonaModule` 工具按 `name` 主动拉正文
- **adding memory**: write `memdir/<type>_<slug>.md` with frontmatter `name` / `description` / `metadata.type`, add a line to `memdir/MEMORY.md`

## Claude Code protocol mapping

Runtime tool name ↔ Claude protocol name (for `settings.json` patterns):

| Runtime           | Claude    |
|-------------------|-----------|
| `bash_exec`       | `Bash`    |
| `file_read`       | `Read`    |
| `file_write`      | `Write`   |
| `web_search`      | `WebSearch` |

A `settings.json` written for Claude works unchanged here. `defaultMode: ask` opens a REPL confirmation prompt; wechat/web channels deny by default since there's no human to confirm.

`settings.json` resolution: project-level `./settings.json` overrides global `~/.sanshiliu/settings.json` (merged, not replaced — same as Claude).

## Configuration

All knobs live in `src/sanshiliu/foundation/config.py` (`Settings` pydantic model). Env vars use the `SANSHILIU_*` prefix; full list and defaults are in the docstring of each field. Required at startup: `OPENAI_API_KEY` (everything else has defaults).

Feature flags (default true unless noted) — flip in `.env` to disable a whole layer:
- `SANSHILIU_TOOLS_ENABLED` — tool_calls
- `SANSHILIU_SKILLS_ENABLED` — SKILL.md loading
- `SANSHILIU_MEMORY_ENABLED` — CLAUDE.md + memdir loading
- `SANSHILIU_SECURITY_ENABLED` — settings.json approval (off = all tools auto-allowed)
- `SANSHILIU_AUTO_EXTRACT_ENABLED` (default **false**) — extra LLM call per turn to extract memory candidates
- `SANSHILIU_WECHAT_ENABLED` (default **false**) — iLink WeChat bot

## Conventions

- Code, comments, and logs are **Chinese**. `ruff` ignores `RUF001/2/3` so full-width punctuation in docstrings/strings is fine. Comments mark *why*, not *what*.
- Layer rule: a module in L4 must not import from L9. If you need a reverse dependency, you're probably routing it wrong — bootstrap injects, not the inner layer.
- `OPENAI_BASE_URL` is auto-stripped of trailing `/` (avoids double-slash 404 with the OpenAI SDK's path concat).
- `pyproject.toml` per-file-ignores deliberately whitelist `do_GET` (HTTP protocol naming) and SENTINEL uppercase locals — don't "fix" them.
- Don't bypass permissions, don't rewrite git history.

## Known gaps (do not claim these work)

Sub-agent audit from 2026-05-23 — relevant when touching these areas:

- `llm/client.py` test coverage is **18%** (retry / stream / error mapping mocks missing)
- `engine/loop.py` coverage **46%** — tool_call loop, dedupe, budget reverse-lookup untested
- `skills/matcher.py` semantic match is a **stub** (keyword path only; embedding not wired)
- `bash_exec` uses shell concat of LLM strings; the classifier is regex-only and can be obfuscation-bypassed. Anything `critical`-tier should hard-deny, not prompt.
- `PermissionManager._session_cache` is unlocked — concurrent channels can double-prompt or double-write `settings.json`
- iLink webhook HMAC has **no timestamp/replay window** — add 5min skew when fixing
- `channels/web/handlers.py healthz` reports wechat status hardcoded to `disabled`

The README's "已知缺口" section is the authoritative checklist before tagging `v1.0.0`.
