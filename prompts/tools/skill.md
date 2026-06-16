---
name: Skill
description: |
  在主对话中执行一个 skill。用户让你做某件事时，先检查「可用技能」列表里有没有匹配的 skill——skill 提供专门的能力与领域知识。用户提到 slash 命令（「/<名字>」，如 /commit）也是指某个 skill，用本工具执行。
  调用方式：skill 参数填技能名（取自列表 name 字段，可带或不带前导斜杠，如 "wechat-style" 或 "/wechat-style"）。
  重要：
  - 可用 skill 列表见 system prompt 的「可用技能（available skills）」一节。
  - 当某个 skill 匹配用户的请求时，这是一条**强制要求（BLOCKING）**：在就该任务生成任何其它回复之前，必须先调用对应的 Skill 工具——别先凭自身知识作答、事后才补调。
  - 绝不要嘴上提到某个 skill 却不实际调用它。
  - 一轮对话里同一个 skill 不要重复调用，也不要调用已在运行中的 skill。
  - 本地 skill 命中时优先于 web_search / bash 等通用工具。
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

对齐 Claude Code 的 SkillTool（src/tools/SkillTool/prompt.ts）：用户请求一旦匹配某个 skill，
**在回答前先调用本工具**（BLOCKING REQUIREMENT），而不是先凭模型知识答、事后才补调；
匹配才调、一轮一次，不要把 Skill 当成通用搜索，也不要嘴上提到某个 skill 却不实际调用它。
