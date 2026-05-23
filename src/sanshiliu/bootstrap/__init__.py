"""L1 启动层；preflight 环境检查 + setup_wizard 交互向导 + wire 一键装配。"""

from sanshiliu.bootstrap.banner import format_banner, render_status_summary
from sanshiliu.bootstrap.install import detect_missing_dependencies, run_install_wizard
from sanshiliu.bootstrap.preflight import PreflightReport, run_preflight
from sanshiliu.bootstrap.setup_wizard import run_setup_wizard
from sanshiliu.bootstrap.wire import App, build_app

__all__ = [
    "App",
    "PreflightReport",
    "build_app",
    "detect_missing_dependencies",
    "format_banner",
    "render_status_summary",
    "run_install_wizard",
    "run_preflight",
    "run_setup_wizard",
]
