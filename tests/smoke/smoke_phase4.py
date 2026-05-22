"""Phase 4 烟测：起 WebServer 在同进程，httpx 验 /healthz /metrics /chat (SSE)。

直接 `python tests/smoke/smoke_phase4.py` 跑；用真实 DeepSeek 后端。
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx

from sanshiliu.channels.web.handlers import (
    HealthState,
    make_chat_handler,
    make_healthz_handler,
    make_metrics_handler,
)
from sanshiliu.channels.web.routes import Router
from sanshiliu.channels.web.server import WebServer
from sanshiliu.context.manager import ContextManager
from sanshiliu.context.prompts import load_compact_prompts
from sanshiliu.engine.loop import ConversationEngine
from sanshiliu.foundation.config import get_settings
from sanshiliu.foundation.logging import configure_logging, get_logger
from sanshiliu.identity.loader import PersonaLoader
from sanshiliu.llm.client import LLMClient
from sanshiliu.storage.db import get_database

_logger = get_logger(__name__)

# 用非默认端口避免和实际生产服务冲突
_SMOKE_PORT = 19527


async def main() -> int:
    settings = get_settings()
    configure_logging(log_level="WARNING", log_dir=settings.data_dir / "logs")

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
    cm = ContextManager(
        llm=llm,
        prompts=compact_prompts,
        max_context_tokens=settings.max_context_tokens,
        compact_threshold_ratio=settings.compact_threshold_ratio,
    )
    engine = ConversationEngine(llm=llm, db=db, persona_loader=loader, context_manager=cm)

    health = HealthState()
    health.set("llm", "up")
    health.set("db", "up")
    health.set("web", "up")
    health.set("wechat", "disabled")

    loop = asyncio.get_running_loop()
    router = Router()
    router.register("POST", "/chat", make_chat_handler(engine, loop, health))
    router.register("GET", "/healthz", make_healthz_handler(db, loop, health))
    router.register("GET", "/metrics", make_metrics_handler(cm))

    server = WebServer(host="127.0.0.1", port=_SMOKE_PORT, router=router, loop=loop)
    server.start()
    # 等线程起来
    await asyncio.sleep(0.3)

    v1_ok = False
    v2_ok = False
    v_metrics_ok = False
    v_404_ok = False
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{_SMOKE_PORT}", timeout=120.0) as client:
            # V-2：/healthz 返回 4 项
            print("\n[V-2] GET /healthz")
            r = await client.get("/healthz")
            print(f"  status={r.status_code}, body={r.text[:200]}")
            try:
                data = r.json()
                comps = data.get("components", {})
                v2_ok = all(k in comps for k in ("wechat", "llm", "db", "web"))
                print(f"  components keys: {sorted(comps.keys())}  {'✅' if v2_ok else '❌'}")
            except json.JSONDecodeError:
                print("  ❌ 响应非 JSON")

            # V (附加)：/metrics 返回 budget
            print("\n[附加] GET /metrics")
            r = await client.get("/metrics")
            try:
                data = r.json()
                v_metrics_ok = "budget" in data and "max_tokens" in data["budget"]
                print(f"  budget keys: {sorted(data['budget'].keys())[:5]}... {'✅' if v_metrics_ok else '❌'}")
            except json.JSONDecodeError:
                print("  ❌ 响应非 JSON")

            # 404
            print("\n[附加] GET /nope")
            r = await client.get("/nope")
            v_404_ok = r.status_code == 404
            print(f"  status={r.status_code} {'✅' if v_404_ok else '❌'}")

            # V-1：POST /chat SSE 流式
            print("\n[V-1] POST /chat (SSE)")
            chunks: list[str] = []
            events: list[str] = []
            async with client.stream(
                "POST", "/chat", json={"q": "你好，一句话自我介绍。"},
                headers={"Accept": "text/event-stream"},
            ) as resp:
                print(f"  status={resp.status_code}, content-type={resp.headers.get('content-type')}")
                if resp.status_code == 200 and "text/event-stream" in (resp.headers.get("content-type") or ""):
                    current_event = "message"
                    current_data: list[str] = []
                    async for line in resp.aiter_lines():
                        if line.startswith("event: "):
                            current_event = line[len("event: "):].strip()
                        elif line.startswith("data: "):
                            current_data.append(line[len("data: "):])
                        elif line == "":
                            # 一帧结束
                            if current_data:
                                payload = "\n".join(current_data)
                                if current_event == "message":
                                    chunks.append(payload)
                                events.append(current_event)
                                current_data = []
                                current_event = "message"
                                if events[-1] == "done":
                                    break
                                if events[-1] == "error":
                                    print(f"  [SSE error] {payload}")
                                    break
            reply = "".join(chunks)
            print(f"  收到 {len(chunks)} 帧 data；events={events[-3:]}；回复字符={len(reply)}")
            print(f"  reply 前 80 字：{reply[:80]}")
            v1_ok = "done" in events and len(reply) > 0
            print(f"  {'✅ V-1' if v1_ok else '❌ V-1'}")

    finally:
        server.stop()
        await llm.close()
        await db.close()

    print("\n── Phase 4 烟测汇总 ──")
    print(f"  V-1 /chat SSE 流式             : {'✅' if v1_ok else '❌'}")
    print(f"  V-2 /healthz 4 项              : {'✅' if v2_ok else '❌'}")
    print(f"  附加 /metrics budget           : {'✅' if v_metrics_ok else '❌'}")
    print(f"  附加 404 行为                  : {'✅' if v_404_ok else '❌'}")

    return 0 if (v1_ok and v2_ok and v_metrics_ok and v_404_ok) else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
