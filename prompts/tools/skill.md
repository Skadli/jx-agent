---
name: Skill
description: |
  按名调用一个本地 skill。skill 列表见 system prompt 中「可用技能」一节，每项给出 name 与 description；
  当用户的请求与某个 skill 的 description 描述的场景相符时，调用本工具拿到该 skill 的完整正文（SKILL.md 主体），
  随后按正文里的步骤继续推进。一轮对话里同一个 skill 不要重复调用。
parameters:
  type: object
  properties:
    skill:
      type: string
      description: skill 名（取自列表里的 name 字段；允许带或不带前导斜杠，例如 "wechat-style" 或 "/wechat-style"）
  required:
    - skill
---

# Skill 工具

把 SKILL.md 协议下的 skill 暴露给 LLM；与 Claude Code 的 SkillTool 概念对齐。

## 行为

- `skill` 参数是 skill id（即 `skills/<id>/SKILL.md` 中 frontmatter 的 `name`，亦即目录名）。
- 命中：返回该 skill 的正文（去掉 frontmatter 的 markdown body）。同时向 `skill_activations` 写一条审计。
- 未命中：返回 `is_error=true` 的简短提示；不写库。

## 调用时机

只在用户需求清晰匹配某个 skill 的描述时调用一次；不要把 Skill 当成通用搜索。
