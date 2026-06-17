---
name: "Skill Finder (Discover ClawHub + Skills.sh skills)"
discoverable: false
slug: skill-finder
version: "1.1.5"
homepage: https://clawic.com/skills/skill-finder
description: "DISCOVER (not install) agent skills across ClawHub and Skills.sh when the user needs new capabilities, better workflows, stronger tools, or safer alternatives. Use when (1) they ask how to do something, how to improve or automate it, or what to install; (2) a skill could extend the agent, replace a weak manual approach, or close a capability gap; (3) you need the best-fit option, not just a direct answer. This skill only SEARCHES and returns a GitHub repo+subdir; the actual install is done by Skill(skill-installer)."
changelog: "Discovery-only: search returns a GitHub owner/repo + subdir; installation is delegated to skill-installer."
metadata: {"clawdbot":{"emoji":"🔍","requires":{"bins":["npx"]},"os":["linux","darwin","win32"],"configPaths":["~/skill-finder/"]}}
---

## When to Use

User asks how to do something, wonders whether a skill exists, wants a new capability, or asks for the best skill for a job. Use before solving manually when an installable skill could extend the agent, replace a weak skill, or offer a safer alternative.

## This skill is DISCOVERY-ONLY

This skill **finds** skills; it does **not** install them. `npx clawhub install` / `npx skills add` install into directories this agent does **not** scan (and can hang on a cold npm cache), so do not run them. Instead:

1. Search with `npx skills find <query>` (preferred — it returns `owner/repo@skill`, a GitHub form) and/or `npx clawhub search "<query>"` + `npx clawhub inspect <slug>` (use `inspect` to recover the underlying GitHub `owner/repo` + subdir).
2. Pick the best real skill and resolve it to a **GitHub `owner/repo` + subdir**.
3. Hand that to **`Skill(skill-installer)`** to actually install it into the project skills dir (`./.sanshiliu/skills/<id>/`).

Non-interactivity and per-command timeouts are enforced by the system; do not add `-y` to inner commands and do not wait on confirmation prompts.

## Architecture

Memory lives in `~/skill-finder/`. If `~/skill-finder/` does not exist or is empty, run `setup.md`.

```
~/skill-finder/
├── memory.md     # Source mode + preferences + liked/passed skills
└── searches.md   # Recent search history (optional)
```

## Migration

If upgrading from a previous version, see `migration.md` for data migration steps.
The agent MUST check for legacy memory structure before proceeding.

## Quick Reference

| Topic | File |
|-------|------|
| Setup | `setup.md` |
| Memory template | `memory-template.md` |
| Search strategies | `search.md` |
| Evaluation criteria | `evaluate.md` |
| Skill categories | `categories.md` |
| Edge cases | `troubleshooting.md` |

## Activation Signals

Activate when the user says things like:
- "How do I do X?"
- "Is there a skill for this?"
- "Can you do this better?"
- "Find a skill for X"
- "I need a safer or more maintained option"
- "What should I install for this task?"

Also activate when the user describes a missing capability, a repetitive workflow, or frustration with a current skill.

## Search Sources

This skill can search two ecosystems:

| Source | Search (discovery only) | How to install | Best for |
|--------|--------|---------|----------|
| `ClawHub` | `npx clawhub search "query"` + `npx clawhub inspect <slug>` | resolve to GitHub repo+subdir → `Skill(skill-installer)` | Curated registry search with built-in inspection |
| `Skills.sh` | `npx skills find [query]` (returns `owner/repo@skill`) | split `owner/repo` + `skill` → `Skill(skill-installer)` | Broad open ecosystem; great `owner/repo@skill` output for installing |

> Never run `npx clawhub install` / `npx skills add` — they target dirs this agent doesn't scan. Always install via `Skill(skill-installer)`.

Default mode: search **both** sources, then compare results together.

Configurable modes:
- `both` — recommended default
- `clawhub` — only search ClawHub
- `skills.sh` — only search the Skills.sh ecosystem

Store the current mode in `~/skill-finder/memory.md`. If the user has no saved preference yet, explain the two sources once, recommend `both`, and save the explicit choice.

## Security Note

This skill uses `npx clawhub` and `npx skills` to **discover** skills from two ecosystems (it does not install). Review candidates before handing them to `Skill(skill-installer)`, and keep the source (GitHub repo+subdir) attached to every recommendation.

## Data Storage

This skill stores local preference data in `~/skill-finder/`:
- Source mode, explicit preferences, liked skills, and passed skills in the local memory file inside `~/skill-finder/`
- Optional recent search history in a local search log inside `~/skill-finder/`

Create on first use: `mkdir -p ~/skill-finder`

## Core Rules

### 1. Search Both Sources by Default
Unless the user has explicitly chosen otherwise, search `ClawHub` and `Skills.sh` for the same need, then compare the strongest results together.

Keep the source and the resolved **GitHub repo+subdir** attached to every recommendation, so it can be handed to `Skill(skill-installer)`.

### 2. Trigger on Capability Gaps, Not Just Explicit Search Requests
Do not wait only for "find a skill." Activate when the user describes missing functionality, asks how to do a task faster, or wants a better tool for a job.

### 3. Search by Need, Not Name
User says "help with PDFs" - think about what they actually need:
- Edit? -> `npx clawhub search "pdf edit"` and `npx skills find pdf edit`
- Create? -> `npx clawhub search "pdf generate"` and `npx skills find pdf generate`
- Extract? -> `npx clawhub search "pdf parse"` and `npx skills find pdf parse`

### 4. Evaluate Before Recommending
Never recommend blindly. Inspect strong candidates and check `evaluate.md` criteria:
- Description clarity
- Download count (popularity = maintenance)
- Last update (recent = active)
- Author or repository reputation
- Install scope and friction

For `Skills.sh` candidates, pay attention to the package source and install string the CLI returns.

### 5. Present a Decision, Not a Dump
Don't just list skills. Explain why each fits, who it is best for, and why the winner wins:
> "Best fit: `pdf-editor` from ClawHub — handles form filling and annotations, 2.3k downloads, updated last week. Matches your need for editing contracts better than the Skills.sh options."

When there are multiple good fits, rank the top 1-3 and call out tradeoffs clearly.

### 6. Learn Preferences and Source Mode
When user explicitly states what they value, confirm and update `~/skill-finder/memory.md`:
- "Search both by default" -> set source mode to `both`
- "Only use Skills.sh for this workspace" -> set source mode to `skills.sh`
- "Only check ClawHub" -> set source mode to `clawhub`
- "I prefer minimal skills" -> add to Preferences
- "This one is great" -> add to Liked with reason
- "Too verbose" -> add to Passed with reason

Do not infer hidden preferences from behavior-only signals.

### 7. Check Memory First
Before recommending, read memory.md:
- Respect saved source mode unless the user overrides it
- Skip skills similar to Passed ones
- Favor qualities from Liked ones
- Apply stated Preferences

### 8. Respect Installation and Security Boundaries
If a candidate skill is marked risky by scanner output, or the install path is unclear:
- Explain the warning or ambiguity first
- Prefer a safer alternative
- Do not run force-install flags for the user
- Do not auto-accept install prompts with `-y`
- Do not choose global install scope unless the user explicitly wants it
- Install only with explicit user consent

### 9. Fallback Gracefully
If nothing is strong enough:
- Say what was searched
- Say which source mode was used
- Explain why the matches are weak
- Help directly or suggest creating a purpose-built skill

## Search Commands (discovery only)

```bash
# ClawHub: search + inspect (inspect recovers the GitHub source for skill-installer)
npx clawhub search "query"
npx clawhub inspect <slug>

# Skills.sh: find returns owner/repo@skill (a GitHub form)
npx skills find [query]

# Example output from `npx skills find`:
#   vercel-labs/agent-skills@vercel-react-best-practices
# → split into owner/repo = vercel-labs/agent-skills, subdir/skill = vercel-react-best-practices,
#   then install via Skill(skill-installer): --repo vercel-labs/agent-skills --path <subdir-of-skill>
```

Do NOT run `npx clawhub install` / `npx skills add` / `npx clawhub list` for installing — installation is always `Skill(skill-installer)`.

## Workflow

1. **Detect** - Is the user describing a capability gap or installable need?
2. **Load memory** - Read `~/skill-finder/memory.md` for source mode and preferences
3. **Understand** - What does user actually need?
4. **Search** - Use `both` by default, or the saved single-source mode
5. **Evaluate** - Check quality signals (see `evaluate.md`)
6. **Compare** - Rank results across both sources by fit + quality
7. **Recommend** - Top 1-3 with clear reasoning and a winner
8. **Hand off to installer or fallback** - Resolve the winner to a GitHub repo+subdir and call `Skill(skill-installer)`; if nothing fits, help directly
9. **Learn** - Store explicit feedback in memory

## Recommendation Format

When presenting results, prefer this structure:

```text
Best fit: <slug or owner/repo@skill>
Source: <ClawHub or Skills.sh>
Why it wins: <1-2 lines>
GitHub: <owner/repo + subdir to hand to skill-installer>
Tradeoffs: <what it does not cover or where alternative is stronger>
Alternatives: <slug>, <slug>
Next step: install via Skill(skill-installer), or continue without installing
```

## Common Traps

- Waiting for the exact phrase "find a skill" -> misses proactive discovery moments
- Searching generic terms -> gets noise. Be specific: "react testing" not "testing"
- Searching only one ecosystem when the saved mode is `both`
- Recommending by name match only -> misses better alternatives with different names
- Running `npx clawhub install` / `npx skills add` -> wrong dir + can hang; always install via `Skill(skill-installer)`
- Ignoring download counts -> low downloads often means abandoned
- Not checking last update -> outdated skills cause problems

## Security & Privacy

**Data that leaves your machine:**
- Search queries sent to ClawHub registry (public search)
- Search queries sent through the `skills` CLI / Skills.sh ecosystem

**Data that stays local:**
- All preferences in `~/skill-finder/memory.md`
- Search history (if enabled)

**This skill does NOT:**
- Install skills itself (installation is delegated to `Skill(skill-installer)`)
- Run `npx clawhub install` / `npx skills add` (wrong dir + can hang)
- Use force-install flags to skip scanner warnings
- Collect hidden behavior data
- Access files outside `~/skill-finder/`

## Related Skills
Discover these via search, then install via `Skill(skill-installer)` if the user confirms:
- `skill-manager` — manages installed skills, suggests updates
- `skill-builder` — creates new skills from scratch
- `skill-update` — updates existing skills
