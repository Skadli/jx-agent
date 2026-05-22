"""命令行入口分发。

子命令 (Phase 1 仅 repl)：
    sanshiliu repl    进入交互式对话 (默认)
    sanshiliu --version

后续 phase 会陆续加入 bot (Phase 4) / serve (Phase 4) / doctor (Phase 9) 等。
"""

from __future__ import annotations

import argparse
import sys

from sanshiliu import __version__


def build_parser() -> argparse.ArgumentParser:
    """构造主解析器与子命令解析器。

    单独抽出便于在测试中复用，避免重复 boilerplate。
    """
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
    # Phase 4 / 9 占位，明确声明，避免用户敲了之后看到 KeyError 一脸懵
    sub.add_parser("bot", help="[Phase 4] 启动 iLink 微信 bot")
    sub.add_parser("serve", help="[Phase 4] 启动 HTTP 服务")
    sub.add_parser("doctor", help="[Phase 9] 环境检查向导")

    return parser


def main(argv: list[str] | None = None) -> int:
    """主入口。返回 shell 退出码，0 表示成功。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    # 无子命令时默认进 REPL，对齐 prd Phase 9 启动顺序的体验
    cmd = args.cmd or "repl"

    if cmd == "repl":
        # Ticket 1-8 后会替换为真实 REPL 调用；现在先返回 stub 让 1-V1 (--version) 能验
        from sanshiliu.channels.repl.main import run_repl_sync

        return run_repl_sync()

    if cmd in {"bot", "serve", "doctor"}:
        print(f"[尚未实现] 子命令 '{cmd}' 将在后续 phase 完成", file=sys.stderr)
        return 2

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
