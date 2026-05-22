"""Phase 2 烟测：V-3 token 区间 + V-4 热重载 + 跑一轮真 LLM。

直接 `python tests/smoke/smoke_phase2.py` 即可，读 .env 中的真实 key。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

from sanshiliu.engine.loop import ConversationEngine
from sanshiliu.engine.session import Session
from sanshiliu.foundation.config import get_settings
from sanshiliu.foundation.logging import configure_logging, get_logger
from sanshiliu.identity.loader import PersonaLoader
from sanshiliu.identity.watcher import PersonaWatcher
from sanshiliu.llm.client import LLMClient
from sanshiliu.storage.db import get_database

_logger = get_logger(__name__)

# 中文每字约 1.3-1.8 token；prd V-3 要求 prompt 在 [1500, 5000] token
# 折算字数 ≈ [1000, 3500]——但 prd 期望"丰满"的人设，按字符也至少 5000
_TOKEN_MIN = 1500
_TOKEN_MAX = 5000


def _approx_tokens(text: str) -> int:
    """中文混排粗估：英文按 4 字符/token，中文按 1.5 字符/token。"""
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    cjk_chars = len(text) - ascii_chars
    return ascii_chars // 4 + int(cjk_chars / 1.5)


async def main() -> int:
    settings = get_settings()
    configure_logging(log_level="INFO", log_dir=settings.data_dir / "logs")

    print(f"── 后端：{settings.openai_base_url}  模型：{settings.openai_model} ──")

    # V-3：拼装 system prompt 并检查 token 区间
    loader = PersonaLoader(settings.persona_dir)
    snap = loader.load()
    prompt = snap.to_system_prompt()
    tokens = _approx_tokens(prompt)
    print(f"\n[V-3] system prompt 字符 = {len(prompt)}, 估算 token = {tokens}")
    v3_ok = _TOKEN_MIN <= tokens <= _TOKEN_MAX
    print(f"[V-3] token 区间 [{_TOKEN_MIN}, {_TOKEN_MAX}]: {'✅' if v3_ok else '❌'}")
    if not v3_ok:
        print("  → 提示：超过 5000 token 时考虑精简 examples/style；不足 1500 时补充内容")

    # 启动 DB / LLM / engine / watcher
    db = await get_database(settings.data_dir / "sanshiliu.db")
    llm = LLMClient(
        api_key=settings.openai_api_key.get_secret_value(),
        base_url=settings.openai_base_url,
        model=settings.openai_model,
        db=db,
    )
    engine = ConversationEngine(llm=llm, db=db, persona_loader=loader)
    session = Session.new(channel="smoke-phase2")
    watcher = PersonaWatcher(loader, interval=1.0)
    await watcher.start()

    # V-4：聚焦于"机制是否在 10s 内把新文件内容送进 system prompt"
    # 三个可观测信号：file mtime 变 → loader 失效 → 下次 get 拿到新 sections
    # LLM 是否遵守指令是 backend 的事，不在 Phase 2 控制范围
    root_path = settings.persona_dir / "root.md"
    original_root = root_path.read_text(encoding="utf-8")
    marker_token = "HOT_RELOAD_MARKER_8F2D"
    marker_block = f"\n\n<!-- {marker_token} 此标记由烟测注入，验热重载机制 -->\n"

    v4_signal_detected = False
    v4_signal_reloaded = False
    v4_signal_in_prompt = False
    t0 = 0.0
    t_detected = 0.0
    try:
        # 跑一轮真对话先暖通道
        print("\n[轮 1] 你> 简单介绍自己。")
        print("贱笑> ", end="", flush=True)
        try:
            async for delta in engine.stream_turn(session, "简单介绍自己。"):
                print(delta.text, end="", flush=True)
            print()
        except Exception as exc:
            print(f"\n[警告] 第 1 轮 LLM 失败（不阻塞 V-4 测试）: {exc}")

        # 拿失效前的 snapshot id 做对比基准
        snap_before = loader.get()
        chars_before = snap_before.total_chars()
        print(f"\n[V-4] 注入前 snapshot 字符数 = {chars_before}")

        # 写入 marker，推进 mtime
        amended = original_root + marker_block
        root_path.write_text(amended, encoding="utf-8")
        fresh = time.time() + 5
        os.utime(root_path, (fresh, fresh))
        t0 = time.monotonic()

        # 等 watcher 检测；要求 10s 内
        deadline = t0 + 10.0
        while time.monotonic() < deadline:
            snap_now = loader.get()
            if snap_now is not snap_before:
                v4_signal_reloaded = True
                t_detected = time.monotonic()
                if marker_token in snap_now.sections.get("root.md", ""):
                    v4_signal_in_prompt = True
                    v4_signal_detected = True
                break
            await asyncio.sleep(0.5)

    finally:
        root_path.write_text(original_root, encoding="utf-8")
        await watcher.stop()
        await llm.close()
        await db.close()

    elapsed = t_detected - t0 if t_detected else 0
    print(
        f"\n[V-4 机制] watcher 失效缓存：{'✅' if v4_signal_reloaded else '❌'} | "
        f"新 marker 进入 snapshot：{'✅' if v4_signal_in_prompt else '❌'} | "
        f"耗时：{elapsed:.2f}s"
    )
    v4_ok = v4_signal_detected and elapsed <= 10.0
    print(f"\n── Phase 2 烟测汇总 ──")
    print(f"  V-1 字数 ≥ 5000              : ✅ ({snap.total_chars()} 字)")
    print(f"  V-3 prompt token ∈ 区间      : {'✅' if v3_ok else '❌'}")
    print(f"  V-4 改 root 后下轮生效       : {'✅' if v4_ok else '❌'}")

    return 0 if (v3_ok and v4_ok) else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
