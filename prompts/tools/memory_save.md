---
name: SaveMemory
description: |
  主动写入一条新的长期记忆到 memdir，让自己在未来对话中能记住。
  使用场景（4 类对齐 Claude memdir）：
    - user：用户的偏好 / 习惯 / 背景信息（例：希望被称呼方式、工作领域、口味）
    - feedback：用户对你回答的纠正或确认（例：上次回答方式被批评/被表扬的原因和修正方向）
    - project：当前项目的关键决策 / 约束 / 进度（例：架构选型、deadline、依赖）
    - reference：可复用的资料 / 公式 / 模板（例：常用配置、命令片段、外部链接）
  不要：
    - 把临时上下文当记忆存（一次性的对话内容不应进长期）；
    - 与既有索引条目重复（先看 system prompt memory_block 是否已有同主题）；
    - 把敏感信息（口令/密钥/隐私）写入；
  feedback / project 类型建议 body 用以下结构：
    **Why:** <为什么记这一条>
    **How to apply:** <下次怎么应用>
  相对日期一律转绝对日期。
parameters:
  type: object
  properties:
    name:
      type: string
      description: 唯一短名；字母/数字/短横线/下划线，长度 5-40（如 user_call_boss / feedback_short_reply）
    type:
      type: string
      enum: [user, feedback, project, reference]
      description: 记忆类型，4 选 1
    description:
      type: string
      description: 一行摘要（≤120 字），会作为 MEMORY.md 索引行的可读 hook
    body:
      type: string
      description: 完整正文；feedback/project 建议含 **Why:** 和 **How to apply:**
    confidence:
      type: number
      description: 可选；0-1 的置信度（auto-extract 用，主动写一般可省略）
  required:
    - name
    - type
    - description
    - body
---

# SaveMemory 工具

把当前对话里值得长期记住的事实主动落到 memdir。索引行自动追加到 MEMORY.md，
下一轮起这条记忆的 name + description 就会出现在 system prompt memory_block，body 通过 LoadMemory 取。

## 校验

- name 不能含中文/特殊字符，只允许字母/数字/短横线/下划线，长度 5-40；
- type 必须是 user / feedback / project / reference 四选一；
- 同名条目会作为新文件追加（不覆盖），但索引段不去重——务必先确认未重复。

## 典型示例

用户："以后叫我老板就行"
→ `SaveMemory({"name":"user_call_boss","type":"user","description":"用户希望被称呼为'老板'","body":"用户在 2026-05-27 明确：日常对话中称呼为'老板'。"})`

用户："你上次给的回答太长了，简短点"
→ `SaveMemory({"name":"feedback_short_reply","type":"feedback","description":"用户偏好简短回答","body":"**Why:** 用户 2026-05-27 反馈长答太啰嗦。\\n**How to apply:** 默认 3 句话内回完，除非用户明确要展开。"})`
