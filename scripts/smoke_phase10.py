"""Phase 10 smoke：本地真调一次豆包识图 + DeepSeek 纯文本回归。

用法：
    # 先 export / set 环境变量
    $env:OPENAI_API_KEY = "<deepseek key>"
    $env:OPENAI_BASE_URL = "https://api.deepseek.com"
    $env:DOUBAO_API_KEY = "<doubao key>"
    python -m scripts.smoke_phase10

退出码：
    0 - 通过
    1 - 失败（缺 key / API 错 / 路由错）
"""

from __future__ import annotations

import asyncio
import sys

from sanshiliu.bootstrap.wire import build_app
from sanshiliu.foundation.config import Settings

# 16x16 全红 PNG（豆包 vision 要求最小 14px 边长）；data URI 大约 100 字节
_TINY_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAGElEQVR4nGP4z8BAEiJN9aiGUQ0MQ0kDAJD5/wGaM2eTAAAAAElFTkSuQmCC"
)


async def main() -> int:
    settings = Settings()  # type: ignore[call-arg]
    # 通过 Settings 读 .env；任一缺则不跑
    if settings.doubao_api_key is None:
        print("[FAIL] DOUBAO_API_KEY 未在 .env 配置", file=sys.stderr)
        return 1
    app = await build_app(settings)
    try:
        # 列出 provider，验装配
        specs = app.llm.registry.specs()
        provider_names = [s.name for s in specs]
        print(f"[INFO] providers: {provider_names}")
        if "doubao" not in provider_names:
            print("[FAIL] doubao provider 未注册", file=sys.stderr)
            return 1

        # 1) 纯文本：应走 default（DeepSeek）
        from sanshiliu.engine.session import Session
        sess1 = Session.new(channel="smoke")
        msg1 = await app.engine.complete_turn(sess1, "用一句话介绍你自己")
        print(f"[OK] 文本回复: {msg1.text_only()[:80]}")

        # 2) 多模态：应走 doubao
        sess2 = Session.new(channel="smoke")
        multimodal = [
            {"type": "text", "text": "这张图大概是什么颜色？"},
            {"type": "image_url", "image_url": {"url": _TINY_PNG}},
        ]
        msg2 = await app.engine.complete_turn(sess2, multimodal)
        print(f"[OK] 图片回复: {msg2.text_only()[:80]}")

        # 3) 验证 llm_calls 表至少含两个不同 base_url
        cur = await app.db._execute(
            "SELECT DISTINCT base_url FROM llm_calls WHERE channel = 'smoke'",
        )
        rows = cur.fetchall()
        base_urls = {r["base_url"] for r in rows}
        print(f"[INFO] base_urls in llm_calls: {base_urls}")
        if len(base_urls) < 2:
            print(f"[FAIL] 期望 ≥ 2 个不同 base_url，实际 {len(base_urls)}", file=sys.stderr)
            return 1
        if not any("volces" in u or "ark" in u for u in base_urls):
            print("[FAIL] llm_calls 中未见豆包 base_url（含 volces/ark）", file=sys.stderr)
            return 1
        if not any("deepseek" in u for u in base_urls):
            print("[FAIL] llm_calls 中未见 DeepSeek base_url", file=sys.stderr)
            return 1

        # 4) 验 by_provider 聚合
        agg = await app.db.aggregate_overview(since_ms=0)
        by_provider = agg.get("by_provider", {})
        print(f"[INFO] by_provider: {by_provider}")
        if len(by_provider) < 2:
            print("[FAIL] by_provider 期望 ≥ 2 个 base_url 分组", file=sys.stderr)
            return 1

        print("[PASS] Phase 10 smoke 通过")
        return 0
    finally:
        await app.shutdown()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
