"""命令行入口；Phase 9 接入 preflight / setup / doctor / wire。"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sanshiliu import __version__


def build_parser() -> argparse.ArgumentParser:
    """构造主解析器与子命令解析器，便于测试复用。"""
    parser = argparse.ArgumentParser(
        prog="sanshiliu",
        description="三十六贱笑 Agent — 通用 agent 框架（协议对齐 Claude Code）",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"sanshiliu {__version__}",
    )

    sub = parser.add_subparsers(dest="cmd", metavar="<command>")
    sub.add_parser("repl", help="进入交互式对话（默认命令）")
    sub.add_parser(
        "serve", help="启动 HTTP 服务（/chat /healthz /metrics），并按 .env 决定是否拉 wechat bot"
    )
    sub.add_parser("bot", help="serve 的别名；强调启动 wechat bot")
    sub.add_parser("doctor", help="环境检查 + 依赖检测；不进入 REPL")
    sub.add_parser("setup", help="配置检查向导：模型名 + 缺 wechat 凭据时扫码连接")
    return parser


def main(argv: list[str] | None = None) -> int:
    """主入口。返回 shell 退出码，0 表示成功。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    cmd = args.cmd or "repl"

    if cmd == "doctor":
        return _run_doctor()

    if cmd == "setup":
        return asyncio.run(_run_setup())

    if cmd == "repl":
        # 首次启动：尝试跑向导（env 已齐则秒过）
        try:
            asyncio.run(_run_setup(skip_if_complete=True))
        except KeyboardInterrupt:
            print("\n(已取消)")
            return 130
        from sanshiliu.channels.repl.main import run_repl_sync

        return run_repl_sync()

    if cmd in {"serve", "bot"}:
        from sanshiliu.channels.web.runner import run_serve_sync

        return run_serve_sync()

    parser.print_help()
    return 0


def _run_doctor() -> int:
    """`sanshiliu doctor`：preflight + 依赖检测 + env 状态；不进 REPL。"""
    from sanshiliu.bootstrap.install import detect_missing_dependencies
    from sanshiliu.bootstrap.preflight import run_preflight

    print("── Preflight 环境检查 ──")
    report = run_preflight()
    for it in report.items:
        mark = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}[it.status]
        print(f"  [{mark}] {it.name:<6} {it.detail}")
        if it.hint and it.status != "ok":
            print(f"        -> {it.hint}")

    print("\n── 依赖检测 ──")
    deps = detect_missing_dependencies()
    for d in deps:
        mark = "OK" if d.installed else "FAIL"
        print(f"  [{mark}] {d.pip_spec}  {d.detail}")
    missing = [d for d in deps if not d.installed]

    print("\n── Persona ──")
    _print_persona_doctor_section()

    if report.has_failures:
        print("\n[ERROR] preflight 有阻塞项；请按提示修复后重试")
        return 78
    if missing:
        print(f"\n[WARN] 缺 {len(missing)} 个依赖；运行 `python -m sanshiliu` 时会引导自动装")
        return 0

    print("\n[OK] 所有检查通过")
    return 0


def _print_persona_doctor_section() -> None:
    """doctor 命令的 persona 段输出；任何异常都吞掉不阻塞 doctor。"""
    try:
        from sanshiliu.foundation.config import get_settings
        from sanshiliu.identity.loader import PersonaLoader
        from sanshiliu.identity.module_loader import PersonaModuleLoader
    except Exception as exc:  # pragma: no cover
        print(f"  [ERROR] 模块导入失败：{exc}")
        return

    try:
        settings = get_settings()
    except Exception as exc:
        print(f"  [WARN] 无法解析 settings（很可能缺 OPENAI_API_KEY）：{exc}")
        print("         persona 段需要 settings.persona_dir，跳过")
        return

    persona_dir = settings.persona_dir
    print(f"  根目录       : {persona_dir}")

    try:
        loader = PersonaLoader(persona_dir)
        snap = loader.load()
    except Exception as exc:
        print(f"  [FAIL] core 加载失败：{exc}")
        return
    print(f"  core/        : {len(snap.sections)} 个文件 / {snap.total_chars()} 字")
    for name in snap.file_order:
        print(f"    {name:<24} {len(snap.sections.get(name, '')):>6} 字")

    try:
        module_loader = PersonaModuleLoader(persona_dir)
        mods = module_loader.load()
    except Exception as exc:
        print(f"  [WARN] modules 加载失败：{exc}")
        return
    if not mods:
        print("  modules/     : (无；按需加载未启用)")
        return
    print(f"  modules/     : {len(mods)} 个文件")
    for m in mods:
        kws = ", ".join(m.trigger_keywords[:5]) or "(无)"
        print(f"    {m.id:<24} {len(m.body):>5} 字  ← {kws}")


async def _run_setup(*, skip_if_complete: bool = False) -> int:
    """`sanshiliu setup`：跑 setup_wizard；env 完整时按 skip_if_complete 决定是否略过。"""
    try:
        from sanshiliu.bootstrap.setup_wizard import run_setup_wizard
    except ImportError as exc:
        print(f"setup 模块加载失败：{exc}", file=sys.stderr)
        return 78

    # 用 settings 解析 home_dir；缺 env 也能跑（home_dir 走 default_factory）
    import os
    from pathlib import Path

    raw = os.environ.get("SANSHILIU_HOME_DIR")
    home_dir = Path(raw) if raw else Path.home() / ".sanshiliu"

    result = await run_setup_wizard(
        home_dir,
        force=not skip_if_complete,
        project_env_path=Path.cwd() / ".env",
    )
    if result is None:
        # env 已完整
        return 0
    if not result.completed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
