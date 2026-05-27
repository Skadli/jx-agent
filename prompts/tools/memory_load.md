---
name: LoadMemory
description: |
  按 name 字段同名分流读取：
    - name 是 memdir slug（如 `user_call_boss`）→ 返一条长期记忆的 frontmatter + body；
    - name 是 UUID（如 `34000a24-67f7-4a44-bdc3-c36d2f52b6a5`）→ 返该 session 的
      compact_summary + 最近 N 条 message（N=tail，默认 10，上限 50）；
    - name 是 magic `"recent"` → 同 channel+user_id 下"非当前"最近一个 session 的同上内容。
  使用场景：
    - 在 system prompt memory_block 索引段看到某条 memdir 记忆的 name，想看具体 body；
    - 看到 `## Recent Sessions` 段列出的某个历史 session id，需要回顾之前聊了什么；
    - 用户说"接着上次那个话题继续"——调 `LoadMemory({"name":"recent"})` 取上次内容。
  不要：
    - 一上来盲扫全部 memdir（索引段已给出 name + description）；
    - 已经在 system prompt 看到完整 body 的条目再重复加载；
    - 对当前正在进行的 session 自身调用（system 里看到的 Recent Sessions 段已排除自己）。
parameters:
  type: object
  properties:
    name:
      type: string
      description: |
        memdir entry 的 name / session UUID / "recent" 三选一；
        UUID 走 session 路径，其他按 memdir 查询。
    tail:
      type: integer
      description: 仅 session 路径生效——返最近多少条 message，默认 10，最大 50
  required:
    - name
---

# LoadMemory 工具

## memdir 用法（与原版相同）

system 中看到 `- [user_naming_pref](user_naming_pref_xxx.md) — 用户希望被称呼为"老板"`
→ 用户问"你还记得我喜欢被怎么称呼吗？"
→ 调 `LoadMemory({"name":"user_naming_pref"})` 拉 body 确认细节。

## session 用法

system memory_block 末尾的 `## Recent Sessions (last 5)` 段会列出最近 5 个同 channel+user_id 的
其他 session id。需要回顾时：

- `LoadMemory({"name":"34000a24-67f7-4a44-bdc3-c36d2f52b6a5"})` — 拉指定 UUID 的 compact_summary + 最近 10 条；
- `LoadMemory({"name":"34000a24-...", "tail":30})` — 显式要 30 条；
- `LoadMemory({"name":"recent"})` — magic：自动取同通道下最近一个非当前 session。

## 找不到时

memdir 路径返 is_error + 可用条目前 10 个 name；
session 路径返 is_error（session 不存在 / jsonl 空 / 没有可查的历史 session）。
