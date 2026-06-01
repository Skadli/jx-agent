"""Phase 9 环境检查；步骤 1-2：Python 版本 / venv 状态。

非交互函数，只生成 PreflightReport；交互逻辑放 cli。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import sys
from dataclasses import dataclass, field
from typing import Literal

from sanshiliu.foundation.logging import get_logger

_logger = get_logger(__name__)

# 与 pyproject.toml 一致；任何变更需同时改这两处
_MIN_PYTHON = (3, 13)

CheckStatus = Literal["ok", "warn", "fail"]

# 成长 phase-2 发现 skill 用的 npx 包（skill-finder 的两个生态 CLI）；预热它们暖 ~/.npm/_npx 缓存，
# 让 3am 那一章不必冷拉/不弹"Ok to proceed?"。pin 版本规避 @latest 半夜出 breaking。
_PREWARM_NPX_PKGS = ("clawhub@0.18.0", "skills@1.5.9")

# 预热子进程的非交互 + fail-fast npm 环境（与 growth_runner._GROWTH_NPM_ENV 同义）。
_PREWARM_NPM_ENV = {
    "CI": "true",
    "npm_config_yes": "true",
    "npm_config_fetch_timeout": "20000",
    "npm_config_fetch_retries": "1",
    "npm_config_audit": "false",
    "npm_config_fund": "false",
    "npm_config_progress": "false",
}


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


async def prewarm_npx_for_growth(
    *, enabled: bool, prewarm: bool, timeout_sec: float = 60.0
) -> PreflightItem:
    """serve 启动时 best-effort 预热 npx（暖 ~/.npm/_npx 缓存）；非阻塞，失败只 warn 不抛。

    成长 phase-2 用 `npx clawhub/skills` 发现 skill；服务器冷缓存时**首次** npx 要下载整棵依赖树，
    且无 TTY 时会卡在 "Ok to proceed?" ——这正是实跑里那条挂 84s 的 bash。启动跑一次
    `npx --yes <pkg> --help` 把包灌进缓存，3am 那一章就不必冷拉、也不会再弹确认。

    永不阻塞启动：growth 没开 / 不预热 → 直接 ok-skip；npx 不在 → warn；超时/失败 → warn。
    每个包独立 time-box，总时长由各自 timeout 兜。返回单条 PreflightItem 供日志/dashboard 展示。
    """
    if not enabled or not prewarm:
        return PreflightItem(
            name="npx-prewarm",
            status="ok",
            detail="未启用成长或已关闭预热，跳过 npx 预热",
        )
    npx = shutil.which("npx")
    if npx is None:
        return PreflightItem(
            name="npx-prewarm",
            status="warn",
            detail="未找到 npx；成长 phase-2 装 skill 将不可用（仅传记仍正常）",
            hint="如需自动装 skill，请在服务器装 Node.js（含 npx）",
        )

    child_env = {**os.environ, **_PREWARM_NPM_ENV}
    warmed: list[str] = []
    for pkg in _PREWARM_NPX_PKGS:
        try:
            proc = await asyncio.create_subprocess_exec(
                npx, "--yes", pkg, "--help",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=child_env,
            )
        except OSError as exc:
            _logger.warning("npx 预热启动失败（忽略）", pkg=pkg, error=str(exc))
            continue
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout_sec)
            warmed.append(pkg)
        except TimeoutError:
            # 冷拉太久——杀掉、跳过；3am 真跑时由 bash 硬超时兜底，不在启动这里干等。
            with contextlib.suppress(Exception):
                proc.kill()
                await proc.wait()
            _logger.warning("npx 预热超时（忽略）", pkg=pkg, timeout_sec=timeout_sec)

    if warmed:
        return PreflightItem(
            name="npx-prewarm",
            status="ok",
            detail=f"已预热 npx 缓存：{', '.join(warmed)}",
        )
    return PreflightItem(
        name="npx-prewarm",
        status="warn",
        detail="npx 预热未成功（超时/失败）；3am 成长首次装 skill 可能较慢",
        hint="多为冷缓存或网络受限；不阻塞启动，phase-2 仍会按硬超时尝试",
    )
