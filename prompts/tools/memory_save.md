---
name: SaveMemory
description: |
  主动写入一条新的长期记忆到 memdir；下一轮起 name + description 出现在
  system prompt memory_block 索引，body 通过 LoadMemory 取。
  使用场景：用户表达稳定偏好/反馈/项目决策时主动调；不要存一次性的临时上下文。
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

## 校验

- name 只允许字母/数字/短横线/下划线，长度 5-40，不能含中文；
- 同名条目会追加为新文件不覆盖，索引段不去重——先确认未重复。

## 典型示例

用户："以后叫我老板就行"
→ `SaveMemory({"name":"user_call_boss","type":"user","description":"用户希望被称呼为'老板'","body":"用户在 2026-05-27 明确：日常对话中称呼为'老板'。"})`

用户："你上次给的回答太长了，简短点"
→ `SaveMemory({"name":"feedback_short_reply","type":"feedback","description":"用户偏好简短回答","body":"**Why:** 用户 2026-05-27 反馈长答太啰嗦。\\n**How to apply:** 默认 3 句话内回完，除非用户明确要展开。"})`
