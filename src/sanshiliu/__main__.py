"""支持 `python -m sanshiliu` 启动。

委托给 cli.main —— 与 console_scripts 入口共用同一份解析逻辑。
"""

from __future__ import annotations

from sanshiliu.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
