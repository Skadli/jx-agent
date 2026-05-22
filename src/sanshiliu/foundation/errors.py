"""异常层级。

设计原则：
- 所有自定义异常继承 :class:`SanshiliuError`，便于上层一把抓 except。
- 区分**可重试**和**致命**两类——retry 装饰器据此决定是否退避。
- 异常 message 用中文；用户最终看到的就是这串。
"""

from __future__ import annotations


class SanshiliuError(Exception):
    """所有自定义异常根类。"""


# ── 配置 / 启动 ────────────────────────────────────────────
class ConfigError(SanshiliuError):
    """配置加载、校验失败（缺 env、字段非法等）。"""


# ── LLM 调用 ──────────────────────────────────────────────
class LLMError(SanshiliuError):
    """LLM 调用基类。"""


class LLMRetryableError(LLMError):
    """可重试：429 限流、网络超时、5xx 等。"""


class LLMFatalError(LLMError):
    """不可重试：401 认证失败、400 请求非法、模型不支持 tool_calls 等。"""


# ── 存储 ──────────────────────────────────────────────────
class StorageError(SanshiliuError):
    """sqlite / jsonl 持久化失败。"""


# ── 通道 ──────────────────────────────────────────────────
class ChannelError(SanshiliuError):
    """通道（REPL / wechat / web）层面错误。"""


# ── 上下文（Phase 3 用） ───────────────────────────────────
class ContextError(SanshiliuError):
    """上下文管理错误（compact 失败、token 计算异常等）。"""


# ── 工具（Phase 5 用） ────────────────────────────────────
class ToolError(SanshiliuError):
    """工具执行错误；与 LLM 的 tool_result 中 ``is_error=true`` 对应。"""


class ToolTimeoutError(ToolError):
    """工具执行超时（如 bash_exec 默认 30s）。"""


# ── 权限（Phase 8 用） ────────────────────────────────────
class PermissionDeniedError(SanshiliuError):
    """权限拒绝；含触发的规则字符串便于排查。"""

    def __init__(self, message: str, rule: str | None = None) -> None:
        super().__init__(message)
        self.rule = rule
