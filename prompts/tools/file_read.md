---
name: file_read
description: |
  读本地文件内容；路径必须在当前工作目录内（Phase 8 启用白名单严格校验）。
  支持 offset/limit 读部分行。
parameters:
  type: object
  properties:
    path:
      type: string
      description: 文件路径，相对当前工作目录
    offset:
      type: integer
      description: 起始行号，从 1 开始
      default: 1
    limit:
      type: integer
      description: 读取行数；默认 200
      default: 200
  required:
    - path
---

# File Read 工具

读取本地文本文件。路径会被解析后校验是否在 cwd 子树内。

## 输出格式

每行带行号前缀，便于 LLM 引用：
```
   1\t第一行内容
   2\t第二行内容
```
