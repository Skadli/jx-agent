"""Phase 9 环境检查；步骤 1-2：Python 版本 / venv 状态。

非交互函数，只生成 PreflightReport；交互逻辑放 cli。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Literal

# 与 pyproject.toml 一致；任何变更需同时改这两处
_MIN_PYTHON = (3, 13)

CheckStatus = Literal["ok", "warn", "fail"]


@dataclass(frozen=True)
class PreflightItem:
    """单条检查的结果；fail 会阻塞启动。"""

    name: str
    status: CheckStatus
    detail: str = ""
    hint: str = ""  # 失败时的修复建议


@dataclass(frozen=True)
class PreflightReport:
    """整体报告；ok 表示无 fail；warn 不阻塞但需提示。"""

    items: tuple[PreflightItem, ...] = field(default_factory=tuple)

    @property
    def has_failures(self) -> bool:
        return any(it.status == "fail" for it in self.items)

    @property
    def has_warnings(self) -> bool:
        return any(it.status == "warn" for it in self.items)

    def by_name(self, name: str) -> PreflightItem | None:
        for it in self.items:
            if it.name == name:
                return it
        return None


def _check_python() -> PreflightItem:
    cur = sys.version_info[:2]
    if cur < _MIN_PYTHON:
        return PreflightItem(
            name="python",
            status="fail",
            detail=f"当前 Python {cur[0]}.{cur[1]}；最低需要 {_MIN_PYTHON[0]}.{_MIN_PYTHON[1]}",
            hint="请安装 Python 3.13+，然后重新创建 venv 并运行 python -m pip install -e .",
        )
    return PreflightItem(
        name="python",
        status="ok",
        detail=f"Python {cur[0]}.{cur[1]}.{sys.version_info.micro}",
    )


def _check_venv() -> PreflightItem:
    """是否在 venv；纯参考，不阻塞。"""
    in_venv = (
        sys.prefix != sys.base_prefix or hasattr(sys, "real_prefix")  # 老 virtualenv
    )
    if in_venv:
        return PreflightItem(
            name="venv",
            status="ok",
            detail=f"已在 venv：{sys.prefix}",
        )
    return PreflightItem(
        name="venv",
        status="warn",
        detail="未在 venv 内运行；可能影响依赖隔离",
        hint="建议：python -m venv .venv 后激活，再运行 python -m pip install -e .",
    )


def run_preflight() -> PreflightReport:
    """跑全部检查；返回 PreflightReport 供 cli 决策。"""
    items = (_check_python(), _check_venv())
    return PreflightReport(items=items)
