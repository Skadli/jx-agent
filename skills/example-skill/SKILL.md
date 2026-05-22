---
name: example-skill
description: 示例 skill；展示 SKILL.md 协议结构。命中条件：用户消息含「示例」或「example」关键词。
keywords:
  - 示例
  - example
  - 示例技能
---

# 示例技能

这是一个最小化的 skill 示例，演示 SKILL.md 的协议。命中后这一段会被注入到 system prompt。

## 我能做什么

- 给用户演示 skill 系统怎么工作
- 解释 frontmatter 中的 `name` / `description` / `keywords` 字段

## 触发后的行为

LLM 看到这一段后，应该明确告知用户："已识别为 example-skill 触发场景"。
