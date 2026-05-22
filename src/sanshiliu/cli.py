"""命令行入口；Phase 4 起 serve 已实现，bot/serve 都走 web.runner。"""

from __future__ import annotations

import argparse
import sys

from sanshiliu import __version__


def build_parser() -> argparse.ArgumentParser:
    """构造主解析器与子命令解析器，便于测试复用。"""
    parser = argparse.ArgumentParser(
        prog="sanshiliu",
        description="三十六贱笑 Agent — 通用 agent 框架（协议对齐 Claude Code）",
    )
    parser.add_argument(
        "--version", action="version", version=f"sanshiliu {__version__}",
    )

    sub = parser.add_subparsers(dest="cmd", metavar="<command>")
    sub.add_parser("repl", help="进入交互式对话（默认命令）")
    sub.add_parser("serve", help="启动 HTTP 服务（/chat /healthz /metrics），并按 .env 决定是否拉 wechat bot")
    sub.add_parser("bot", help="serve 的别名；强调启动 wechat bot")
    sub.add_parser("doctor", help="[Phase 9] 环境检查向导")
    return parser


def main(argv: list[str] | None = None) -> int:
    """主入口。返回 shell 退出码，0 表示成功。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    cmd = args.cmd or "repl"

    if cmd == "repl":
        from sanshiliu.channels.repl.main import run_repl_sync
        return run_repl_sync()

    if cmd in {"serve", "bot"}:
        from sanshiliu.channels.web.runner import run_serve_sync
        return run_serve_sync()

    if cmd == "doctor":
        print("[尚未实现] doctor 将在 Phase 9 完成", file=sys.stderr)
        return 2

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
