---
name: file_write
description: |
  写入文本文件；路径必须在当前工作目录内；覆盖式写。父目录会自动创建。
parameters:
  type: object
  properties:
    path:
      type: string
      description: 文件路径，相对当前工作目录
    content:
      type: string
      description: 文件内容（覆盖式）
  required:
    - path
    - content
---

# File Write 工具

覆盖式写入；不存在自动创建；存在则全量替换。

## 安全约束

- 不允许写 cwd 外路径
- 不允许写隐藏文件（以 . 开头的目录）
