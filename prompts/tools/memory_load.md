---
name: LoadMemory
description: |
  按 name 读取一条 memdir 长期记忆的完整内容（frontmatter + body）。
  使用场景：
    - 你在 system prompt 顶部的 memory_block 索引段（MEMORY.md）看到某条记忆的 name，
      想看具体 body 内容再决定如何应用；
    - 用户问到某个偏好/方法/项目细节，你记得索引里有相关条目但不确定细节；
  不要：
    - 一上来盲扫全部 memdir（索引段已给出 name + description，先看够不够）；
    - 已经在 system prompt 看到完整 body 的条目再重复加载。
  返回内容：该条记忆的 frontmatter（type/description/source/confidence/protected）+ body 正文。
parameters:
  type: object
  properties:
    name:
      type: string
      description: 记忆条目的 name 字段；从 MEMORY.md 索引行 `- [name](file.md)` 中复制
  required:
    - name
---

# LoadMemory 工具

按 name 加载一条 memdir 长期记忆的完整文本。索引段（system prompt 顶部 memory_block）
只展示 `- [name](file.md) — description`，body 不在 prompt 里；需要正文时用本工具按需拉。

## 典型示例

system 中看到 `- [user_naming_pref](user_naming_pref_xxx.md) — 用户希望被称呼为"老板"`
→ 用户问"你还记得我喜欢被怎么称呼吗？"
→ 调 `LoadMemory({"name":"user_naming_pref"})` 拉 body 确认细节

## 找不到时

返回 is_error，content 含可用条目前 10 个 name 列表。
