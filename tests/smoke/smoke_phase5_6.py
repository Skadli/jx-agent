"""Phase 5+6 烟测：tool_calls 真实调用 + skill 激活注入 system。

实测两件事：
1. Phase 5 V-2：让 LLM 读 README.md 第一行（file_read 工具）
2. Phase 6 V-2：用户问"我想学剪映" → video-editor skill 被激活、body 进 system prompt
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sanshiliu.context.manager import ContextManager
from sanshiliu.context.prompts import load_compact_prompts
from sanshiliu.engine.loop import ConversationEngine
from sanshiliu.engine.session import Session
from sanshiliu.foundation.config import get_settings
from sanshiliu.foundation.logging import configure_logging
from sanshiliu.identity.loader import PersonaLoader
from sanshiliu.llm.client import LLMClient
from sanshiliu.skills.activator import SkillActivator
from sanshiliu.skills.loader import SkillLoader
from sanshiliu.storage.db import get_database
from sanshiliu.tools.bootstrap import build_tool_stack


async def main() -> int:
    settings = get_settings()
    configure_logging(log_level="WARNING", log_dir=settings.data_dir / "logs")

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
    cm = ContextManager(
        llm=llm, prompts=compact_prompts,
        max_context_tokens=settings.max_context_tokens,
        compact_threshold_ratio=settings.compact_threshold_ratio,
    )
    tool_registry, tool_dispatcher = build_tool_stack(
        prompts_dir=settings.prompts_dir,
        cwd_root=Path.cwd(),
        tavily_api_key=None,
    )
    skill_loader = SkillLoader([settings.skills_dir_project, settings.skills_dir_repo])
    skill_loader.load()
    skill_activator = SkillActivator(skill_loader)

    engine = ConversationEngine(
        llm=llm, db=db, persona_loader=loader, context_manager=cm,
        tool_registry=tool_registry, tool_dispatcher=tool_dispatcher,
        skill_activator=skill_activator,
    )

    v5_tool_ok = False
    v6_skill_ok = False

    try:
        # Phase 6 检查：激活机制 + system prompt 包含 skill body
        print("\n[V-6] skills 加载列表")
        skills = skill_loader.list()
        print(f"  扫到 {len(skills)} 个 skill：{[s.id for s in skills]}")

        actives = skill_activator.activate_for("我想学剪映")
        print(f"\n[V-6 / Phase 6 V-2] '我想学剪映' 激活：{[s.id for s in actives]}")
        assert "video-editor" in [s.id for s in actives], "video-editor 应被激活"

        prompt_addition = skill_activator.to_prompt_addition(actives)
        v6_skill_ok = "video-editor" in [s.id for s in actives] and "视频剪辑助手" in prompt_addition
        print(f"  prompt 增量含 '视频剪辑助手' 段: {'✅' if v6_skill_ok else '❌'}")

        # Phase 5 检查：跑一轮真实 LLM 工具调用
        print("\n[V-5 / Phase 5 V-2] 让 LLM 读 README.md")
        session = Session.new(channel="smoke-p56")
        try:
            msg = await engine.complete_turn(session, "请用 file_read 工具读 README.md 的第 1 行，原样告诉我读到了什么。")
            print(f"  回复（前 200 字）：{msg.content[:200]}")
            # 验证：消息历史中应有 tool 角色（工具被调用过）
            tool_msgs = [m for m in session.messages if m.role == "tool"]
            print(f"  历史中 tool 消息数：{len(tool_msgs)}")
            for tm in tool_msgs[:2]:
                print(f"    - {tm.name}: {tm.content[:100]}")
            v5_tool_ok = len(tool_msgs) > 0
        except Exception as exc:
            print(f"  [警告] LLM 调用失败：{exc}")
    finally:
        await llm.close()
        await db.close()

    print("\n── Phase 5+6 烟测汇总 ──")
    print(f"  Phase 5 V-2 file_read 被调用     : {'✅' if v5_tool_ok else '❌'}")
    print(f"  Phase 6 V-2 skill 命中并入 prompt : {'✅' if v6_skill_ok else '❌'}")
    return 0 if (v5_tool_ok and v6_skill_ok) else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
