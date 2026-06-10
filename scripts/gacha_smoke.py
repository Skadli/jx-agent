"""人生卡池 PR1 冒烟：创始卡迁移 + 直跑锻造一张卡（不经 API）。

用法（.env 已配 OPENAI_API_KEY）：
    python -m scripts.gacha_smoke --migrate-only                  # 只跑创始卡迁移并核对
    python -m scripts.gacha_smoke --chapters 1 --skip-skills      # 抽新卡只锻 1 章（最省冒烟）
    python -m scripts.gacha_smoke --chapters 2 --genre xiuxian    # 指定世界类型
    python -m scripts.gacha_smoke                                 # 完整锻满 + 评级（真·抽卡）

退出码：0 通过；1 失败；78 配置错误。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from sanshiliu.bootstrap.wire import build_app
from sanshiliu.foundation.config import Settings
from sanshiliu.gacha.card_state import (
    ORIGIN_CARD_ID,
    biography_dir,
    create_card,
    load_card_state,
    persona_root,
)
from sanshiliu.gacha.forge_runner import ForgeRunner
from sanshiliu.gacha.migrate import migrate_origin_card
from sanshiliu.gacha.seeds import draw_seed


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="人生卡池 PR1 冒烟")
    p.add_argument("--migrate-only", action="store_true", help="只跑创始卡迁移")
    p.add_argument("--card", default=None, help="续锻已有卡（card_id；跳过抽卡新建）")
    p.add_argument("--chapters", type=int, default=None, help="本次最多锻几章（默认锻满）")
    p.add_argument("--genre", default=None, help="世界类型 id（如 xiuxian；缺省随机）")
    p.add_argument("--prompt", default="", help="自定义补充设定")
    p.add_argument("--creativity", type=float, default=None, help="创意度 0-2（缺省随机）")
    p.add_argument("--skip-skills", action="store_true", help="跳过 phase-2 自动装 skill（快且省）")
    return p.parse_args()


async def main() -> int:
    args = _parse_args()
    try:
        settings = Settings()  # type: ignore[call-arg]  # pydantic-settings 运行时填充
    except Exception as exc:
        print(f"[FAIL] 配置加载失败：{exc}", file=sys.stderr)
        return 78
    gacha_root = settings.data_dir / "gacha"

    # ── 1) 创始卡迁移（幂等） ──
    migrated = migrate_origin_card(
        gacha_root=gacha_root,
        growth_state_path=settings.data_dir / "growth-state.json",
        growth_persona_dir=settings.data_dir / "growth" / "persona",
        memdir_dir=settings.memdir_dir,
        start_age=settings.gacha_start_age,
        years_per_chapter=settings.gacha_years_per_chapter,
        end_age=settings.gacha_end_age,
        birth_year=settings.gacha_birth_year,
    )
    origin = load_card_state(gacha_root, ORIGIN_CARD_ID)
    if origin is None:
        print("[FAIL] 迁移后创始卡仍不可读", file=sys.stderr)
        return 1
    print(
        f"[OK] 创始卡（{'本次迁移' if migrated else '已存在'}）：{origin.title} "
        f"{origin.age} 岁 / 第 {origin.current_chapter}/{origin.end_chapter} 章 / {origin.status}"
    )
    if args.migrate_only:
        return 0

    # ── 2) 抽一张新卡并锻造 ──
    app = await build_app(settings)
    try:
        runner = ForgeRunner(
            engine=app.engine,
            gacha_root=gacha_root,
            persona_dir=settings.persona_dir,
            skill_loader=None if args.skip_skills else app.skill_loader,
            skills_dir_global=None if args.skip_skills else settings.skills_dir_global,
            permission_manager=app.permission_manager,
            db=app.db,
            skill_install_timeout_sec=settings.skill_install_timeout_sec,
            skills_per_card_cap=settings.gacha_skills_per_card_cap,
        )
        if args.card:
            existing = load_card_state(gacha_root, args.card)
            if existing is None:
                print(f"[FAIL] 卡不存在或 card.json 损坏：{args.card}", file=sys.stderr)
                return 1
            card = existing
            print(
                f"[OK] 续锻已有卡：{card.card_id}《{card.title or '—'}》"
                f"第 {card.current_chapter}/{card.end_chapter} 章 / {card.age} 岁"
            )
        else:
            seed = draw_seed(
                genre=args.genre,
                custom_prompt=args.prompt,
                creativity=args.creativity,
                birth_year=settings.gacha_birth_year,
            )
            card = create_card(
                gacha_root,
                seed,
                start_age=settings.gacha_start_age,
                years_per_chapter=settings.gacha_years_per_chapter,
                end_age=settings.gacha_end_age,
            )
            print(
                f"[OK] 抽到种子：{seed.genre_label} | 出身={seed.origin} | "
                f"天赋={'、'.join(seed.talents)} | 触发={seed.trigger} | 创意度={seed.creativity}"
            )
        print(f"[..] 开始锻造 {card.card_id}（max_chapters={args.chapters or '锻满'}）")

        async def on_event(ev: dict[str, Any]) -> None:
            et = ev.get("type")
            if et == "chapter_start":
                print(f"  [章 {ev['chapter']}/{ev['end_chapter']}] {ev['age_range']} 岁 锻造中...")
            elif et == "chapter_done":
                report = str(ev.get("report", ""))[:80].replace("\n", " ")
                print(f"  [章 {ev['chapter']}] 完成（{ev['age']} 岁）：{report}")
            elif et == "skill_installed":
                print(f"  [章 {ev['chapter']}] 装上 skill：{ev['skills']}")
            elif et == "rarity":
                print(
                    f"  [评级] {ev['grade'] or '未评级'} {ev['score']} 分 "
                    f"《{ev['title']}》 {ev['comment']}"
                )
            elif et == "error":
                print(f"  [错误] 第 {ev['chapter']} 章：{ev['message']}")

        state = await runner.forge_card(card.card_id, max_chapters=args.chapters, on_event=on_event)

        # ── 3) 产物核对 ──
        problems: list[str] = []
        if state.current_chapter < 1:
            problems.append("没有任何章成立")
        bio = biography_dir(gacha_root, state.card_id)
        for n in range(1, state.current_chapter + 1):
            if not (bio / f"chapter-{n}.md").is_file():
                problems.append(f"缺传记 chapter-{n}.md")
        proot = persona_root(gacha_root, state.card_id)
        for n in range(0, state.current_chapter + 1):
            ch_dir = proot / f"chapter-{n}"
            if not (ch_dir.is_dir() and any(ch_dir.glob("*.md"))):
                problems.append(f"缺人格快照 chapter-{n}/")
        reloaded = load_card_state(gacha_root, state.card_id)
        if reloaded is None or reloaded.current_chapter != state.current_chapter:
            problems.append("card.json 重读与内存状态不一致")
        if problems:
            print(f"[FAIL] 产物核对未通过：{problems}", file=sys.stderr)
            return 1
        print(
            f"[OK] 卡 {state.card_id} 产物完整：{state.current_chapter} 章 / {state.age} 岁 / "
            f"{state.status} / 评级 {state.rarity.grade or '—'} / 卡名《{state.title or '—'}》"
        )
        return 0
    finally:
        await app.shutdown()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
