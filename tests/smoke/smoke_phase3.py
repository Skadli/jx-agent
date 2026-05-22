"""Phase 3 烟测：V-1 超阈值自动 compact + V-4 /stats 字段。

直接 `python tests/smoke/smoke_phase3.py` 跑；为了快速触发 compact，把 max_context_tokens
降到 1500，几轮真实对话就会过阈值。
"""

from __future__ import annotations

import asyncio
import sys

from sanshiliu.context.manager import ContextManager
from sanshiliu.context.prompts import load_compact_prompts
from sanshiliu.engine.loop import ConversationEngine
from sanshiliu.engine.session import Session
from sanshiliu.foundation.config import get_settings
from sanshiliu.foundation.logging import configure_logging, get_logger
from sanshiliu.identity.loader import PersonaLoader
from sanshiliu.llm.client import LLMClient
from sanshiliu.storage.db import get_database

_logger = get_logger(__name__)


async def main() -> int:
    settings = get_settings()
    configure_logging(log_level="INFO", log_dir=settings.data_dir / "logs")
    print(f"── 后端：{settings.openai_base_url}  模型：{settings.openai_model} ──")

    loader = PersonaLoader(settings.persona_dir)
    loader.load()
    compact_prompts = load_compact_prompts(settings.prompts_dir)
    db = await get_database(settings.data_dir / "sanshiliu.db")
    llm = LLMClient(
        api_key=settings.openai_api_key.get_secret_value(),
        base_url=settings.openai_base_url,
        model=settings.openai_model,
        db=db,
    )
    # 故意调小窗口，几轮就触发 compact
    forced_max = 1500
    cm = ContextManager(
        llm=llm,
        prompts=compact_prompts,
        max_context_tokens=forced_max,
        compact_threshold_ratio=0.8,
    )
    engine = ConversationEngine(llm=llm, db=db, persona_loader=loader, context_manager=cm)
    session = Session.new(channel="smoke-phase3")

    print(f"  强制 max_context_tokens = {forced_max}（阈值 {cm.budget.threshold}）")

    questions = [
        "你叫什么？",
        "今天打算做点啥？",
        "我打算学 Python，给点建议",
        "我手上有个项目，写到一半了，要不要重构？",
        "假设我决定重构，应该先动哪里？",
        "好的，那再说一句鼓励的话吧",
    ]

    triggered_round: int | None = None
    try:
        for i, q in enumerate(questions, 1):
            print(f"\n[轮 {i}] 你> {q}")
            print("贱笑> ", end="", flush=True)
            try:
                async for delta in engine.stream_turn(session, q):
                    print(delta.text, end="", flush=True)
                print()
            except Exception as exc:
                print(f"\n[警告] 轮 {i} 失败（不阻塞）：{exc}")
                continue
            stats = cm.stats()
            print(
                f"  → last_prompt_tokens={stats['last_prompt_tokens']}, "
                f"utilization={stats['utilization']:.1%}, "
                f"compact={stats['compact_count']}, "
                f"summary_chars={len(session.compact_summary)}"
            )
            if stats["compact_count"] >= 1 and triggered_round is None:
                triggered_round = i
    finally:
        await llm.close()
        await db.close()

    stats = cm.stats()
    v1_ok = stats["compact_count"] >= 1
    v4_ok = all(
        k in stats
        for k in (
            "last_prompt_tokens",
            "compact_count",
            "microcompact_count",
            "cache_read",
            "cache_create",
        )
    )

    print("\n── Phase 3 烟测汇总 ──")
    print(f"  V-1 超阈值触发 compact ≥ 1 次     : {'✅' if v1_ok else '❌'} "
          f"({stats['compact_count']} 次，首发轮：{triggered_round})")
    print(f"  V-4 /stats 字段齐全               : {'✅' if v4_ok else '❌'}")
    print(f"  最终 compact_summary 字符        : {len(session.compact_summary)}")
    print(f"  最终消息条数                       : {len(session.messages)}")

    return 0 if (v1_ok and v4_ok) else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
