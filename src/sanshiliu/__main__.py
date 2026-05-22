"""支持 `python -m sanshiliu`，复用 cli.main 解析逻辑。"""

from __future__ import annotations

from sanshiliu.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
