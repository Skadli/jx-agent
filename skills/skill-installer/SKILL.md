---
name: skill-installer
description: Install a skill from a GitHub repo path into the project skills dir (./.sanshiliu/skills/<id>/). Use when you have a GitHub owner/repo + subdir for a real skill (e.g. handed over by skill-finder) and want it installed where the loader scans. Supports private repos.
metadata:
  short-description: Install a skill from a GitHub repo into ./.sanshiliu/skills
---

# Skill Installer

Installs a real skill from a GitHub repo path **into the project skills dir** (`./.sanshiliu/skills/<id>/`), which is exactly where the agent's loader scans — so a freshly installed skill becomes usable immediately. Hand it a GitHub `owner/repo` + subdir (skill-finder produces these) and it does a sparse checkout + copy.

Use the helper script when you have a GitHub repo + subdir for a real skill (e.g. one that `skill-finder` discovered). Install it; it lands in the project skills dir and the loader picks it up.

## Scripts

This script uses network, so when running in a sandbox, request escalation.

- `scripts/install-skill-from-github.py --repo <owner>/<repo> --path <path/to/skill> [<path/to/skill> ...]`
- `scripts/install-skill-from-github.py --url https://github.com/<owner>/<repo>/tree/<ref>/<path>`

Result: the skill lands at `./.sanshiliu/skills/<skill-name>/SKILL.md`, where the loader scans it. No restart needed — the growth system reloads the skills dir after install.

## Behavior and Options

- Defaults to direct download for public GitHub repos.
- If download fails with auth/permission errors, falls back to git sparse checkout (HTTPS first, then SSH).
- Aborts if the destination skill directory already exists.
- **Installs into the project skills dir `./.sanshiliu/skills/<skill-name>` by default** (override via `SANSHILIU_SKILLS_DIR_PROJECT` env, or `--dest <path>`). This is the dir the agent's loader actually scans — do **not** point it at `~/.codex/skills`.
- Multiple `--path` values install multiple skills in one run, each named from the path basename unless `--name` is supplied.
- Options: `--ref <ref>` (default `main`), `--dest <path>`, `--method auto|download|git`.

## Notes

- Private GitHub repos can be accessed via existing git credentials or optional `GITHUB_TOKEN`/`GH_TOKEN` for download.
- Only install **real, existing** skills (a valid `SKILL.md` with `name` + `description` frontmatter). Never hand-author a SKILL.md — that is forbidden.
