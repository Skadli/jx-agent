"""依赖检测 + 交互安装；缺包时直接使用 python -m pip install。"""

from __future__ import annotations

import importlib
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from sanshiliu.foundation.logging import get_logger

_logger = get_logger(__name__)

# 主依赖（与 pyproject.toml [project].dependencies 对齐）
# (import_name, pip_spec)；import 失败则该 spec 进入安装列表
_CORE_DEPENDENCIES: tuple[tuple[str, str], ...] = (
    ("openai", "openai>=1.50.0"),
    ("pydantic", "pydantic>=2.9.0"),
    ("pydantic_settings", "pydantic-settings>=2.6.0"),
    ("structlog", "structlog>=24.4.0"),
    ("httpx", "httpx>=0.27.0"),
    ("yaml", "pyyaml>=6.0.0"),
    ("qrcode", "qrcode>=7.4.0"),
)


@dataclass(frozen=True)
class DependencyStatus:
    """单条依赖检测结果。"""

    import_name: str
    pip_spec: str
    installed: bool
    detail: str = ""


def detect_missing_dependencies(
    deps: Iterable[tuple[str, str]] = _CORE_DEPENDENCIES,
) -> list[DependencyStatus]:
    """逐个 importlib.import_module 检测；返回所有条目（含已装）。"""
    out: list[DependencyStatus] = []
    for import_name, pip_spec in deps:
        try:
            mod = importlib.import_module(import_name)
            ver = getattr(mod, "__version__", "?")
            out.append(
                DependencyStatus(
                    import_name=import_name,
                    pip_spec=pip_spec,
                    installed=True,
                    detail=f"{import_name} {ver}",
                )
            )
        except ImportError as exc:
            out.append(
                DependencyStatus(
                    import_name=import_name,
                    pip_spec=pip_spec,
                    installed=False,
                    detail=str(exc),
                )
            )
    return out


def _build_install_cmd(specs: list[str]) -> list[str]:
    """用当前 Python 对应的 pip 安装，避免 PATH 上的 pip 指向别的解释器。"""
    return [sys.executable, "-m", "pip", "install", *specs]


def run_install_wizard(
    statuses: list[DependencyStatus],
    *,
    confirm: bool = True,
    confirmer: Callable[[str], str] | None = None,
) -> tuple[bool, list[str]]:
    """对缺失依赖跑交互确认 + 安装；返回 (success, missing_after)。

    confirmer：可注入的 callable[[str], str]，便于测试；默认走 input()。
    confirm=False 时直接装不问，用于 CI/E2E。
    """
    missing = [s for s in statuses if not s.installed]
    if not missing:
        return True, []

    specs = [s.pip_spec for s in missing]
    print("\n── 检测到缺失依赖 ──")
    for s in missing:
        print(f"  - {s.pip_spec}")

    if confirm:
        ask = _safe_input if confirmer is None else confirmer
        answer = ask("是否现在自动安装？[Y/n] ").strip().lower()
        if answer not in ("", "y", "yes"):
            print("已取消安装；后续启动会再次检测。")
            return False, specs

    cmd = _build_install_cmd(specs)
    print(f"  $ {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, check=False, capture_output=False)
    except FileNotFoundError as exc:
        _logger.error("安装命令不可用", error=str(exc), cmd=cmd)
        print(f"  [ERROR] 安装失败：{exc}")
        return False, specs

    if proc.returncode != 0:
        print(f"  [ERROR] 安装失败 (exit {proc.returncode})；请手动 `{' '.join(cmd)}`")
        return False, specs

    # 再检测一次确认
    re_check = detect_missing_dependencies([(s.import_name, s.pip_spec) for s in missing])
    still_missing = [s.pip_spec for s in re_check if not s.installed]
    if still_missing:
        print(f"  [WARN] 仍有未装成功的依赖：{still_missing}")
        return False, still_missing
    print("  [OK] 所有依赖已就位")
    return True, []


def _safe_input(prompt: str) -> str:
    """EOF / Ctrl-C 视作取消。"""
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return "n"
