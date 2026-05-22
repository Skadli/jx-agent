"""三十六贱笑 Agent 顶层包。

设计原则：协议尽量对齐 Claude Code。
版本号同时被 cli `--version`、bootstrap banner 和 pyproject.toml 引用，修改时三处同步。
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = ["__version__"]
