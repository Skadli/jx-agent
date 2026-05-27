---
name: bash_exec
description: |
  执行 shell 命令；30s 超时；stdout/stderr 截断到 8000 字符。
  危险命令需要用户交互确认。
parameters:
  type: object
  properties:
    command:
      type: string
      description: 要执行的命令字符串
    timeout_sec:
      type: integer
      description: 超时秒数；默认 30
      default: 30
  required:
    - command
---

# Bash Exec 工具

包装 subprocess.run；Windows 走 cmd，类 Unix 走 /bin/sh。

## 安全约束

- 默认 30s 超时，超时杀进程返 timeout
- 输出超过 8000 字符截断
- Phase 8 接入危险命令分类器（rm -rf 等）
