"""锻造执行器：把一张人生卡从当前章连跑到定格（或限定章数），逐章产传记 + 演化人格。

phase-1 机制平移自 scheduler/growth_runner（老链路冻结待退役），多实例化差异：

- **状态/产物全落卡目录**：card.json 状态机、biography/chapter-N.md 传记、persona/chapter-N/
  人格链——**传记不再写 memdir**（卡是独立的人，进本体记忆会污染多卡边界，设计 §3 硬边界）。
- **逐章循环**：老链路一次心跳一章；这里 forge_card 一口气连跑到 end_chapter（同步流式，
  设计决策 #1），max_chapters 可限章（冒烟/分段锻造），每章推进即落盘——中断后续锻无损。
- **进度回调 on_event**：每章发 chapter_start / chapter_done / skill_installed 等事件，
  PR2 的 SSE 端点直接桥接它；回调异常只记日志，绝不影响锻造。
- **不触碰 PersonaLoader / 本体人格**：卡锻造期不 invalidate、不改激活人格——只有转生
  （PR3）才动本体。锻造用的合成 session（channel=gacha）仍由 engine 注入本体当前 persona
  与记忆块作底色，这与老成长链路同语义（分身做梦）；卡叙事以 prompt 中的命运种子为准。
- **跑完评级**：锻满 end_chapter 后调 rarity.grade_card（best-effort）定级 + 命名，再定格。

phase-1 的"必出"保障原样保留：零工具（use_tools=False）纯生成、一次 LLM 修复重发、
schema 校验（narrative 强制非空）；触顶/修复后仍畸形/落盘失败 → raise ForgeChapterError，
卡标 error（已成立的章保留，可续锻重试）。phase-2 装 skill 经 SkillAutoInstaller
（best-effort、绝不回退已成立的章），预算 = 每章 ≤3 且全卡 ≤ skills_per_card_cap。
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sanshiliu.engine.loop import TOOL_TURN_LIMIT_MESSAGE
from sanshiliu.engine.session import Session
from sanshiliu.foundation.logging import get_logger
from sanshiliu.gacha.card_persona import (
    PROTOCOL_FILENAME,
    filter_persona_sections,
    snapshot_base_core_to_chapter0,
    write_chapter_persona,
)
from sanshiliu.gacha.card_state import (
    CardState,
    ChapterRecord,
    biography_dir,
    biography_path,
    load_card_state,
    persona_root,
    save_card_state,
)
from sanshiliu.gacha.rarity import grade_card
from sanshiliu.gacha.skill_autoinstall import (
    SkillAutoInstaller,
    coerce_learned_items,
    coerce_skill_intents,
    derive_skill_install_intents,
)
from sanshiliu.gacha.structured import parse_structured_output
from sanshiliu.identity.types import CORE_DIRNAME

if TYPE_CHECKING:
    from sanshiliu.engine.loop import ConversationEngine
    from sanshiliu.security.permission import PermissionManager
    from sanshiliu.skills.loader import SkillLoader
    from sanshiliu.storage.db import Database

_logger = get_logger(__name__)

_GACHA_CHANNEL = "gacha"
_GACHA_USER_ID = "gacha"

# phase-1 传记是零工具纯生成（1 轮即出 JSON），留余量给偶发；use_tools=False 实际到不了上限。
_PHASE1_MAX_TURNS = 4

# 锻造事件回调：PR2 的 SSE 端点桥接它；事件是 dict（type + 业务字段），见 forge_card 各 emit 点。
OnForgeEvent = Callable[[dict[str, Any]], Awaitable[None]]


class ForgeChapterError(RuntimeError):
    """某章锻造不可恢复失败（区别于"已定格"这种正常 no-op）。

    由 _run_one_chapter 在以下致命场景 raise：complete_turn 失败、tool 触顶、JSON 修复后
    仍不合法、narrative 为空、传记落盘失败。forge_card 据此把卡标 error 并上抛——
    已成立的章全部保留，续锻（再次 forge_card）从断点重试。
    """


class ForgeRunner:
    """读卡状态 → 逐章（拼 prompt → engine 跑 → 解析 → 落盘 → 推进）→ 跑完评级定格。"""

    def __init__(
        self,
        *,
        engine: ConversationEngine,
        gacha_root: Path,
        persona_dir: Path,
        skill_loader: SkillLoader | None = None,
        skills_dir_global: Path | None = None,
        permission_manager: PermissionManager | None = None,
        db: Database | None = None,
        skill_install_timeout_sec: int = 60,
        skills_per_card_cap: int = 10,
    ) -> None:
        self._engine = engine
        self._gacha_root = gacha_root
        # base persona 来源（persona_dir/core 在卡首章前快照成 chapter-0 出生底版）
        self._persona_dir = persona_dir
        self._skills_per_card_cap = skills_per_card_cap
        # phase-2 安装器：loader + 全局目录齐全才构造；缺任一则整张卡跳过自动装 skill
        # （冒烟脚本 --skip-skills 就是不传这两个）。
        self._installer: SkillAutoInstaller | None = None
        if skill_loader is not None and skills_dir_global is not None:
            self._installer = SkillAutoInstaller(
                skill_loader=skill_loader,
                skills_dir_global=skills_dir_global,
                permission_manager=permission_manager,
                db=db,
                timeout_sec=skill_install_timeout_sec,
            )

    async def forge_card(
        self,
        card_id: str,
        *,
        max_chapters: int | None = None,
        on_event: OnForgeEvent | None = None,
    ) -> CardState:
        """把一张卡从当前章锻到定格（或最多 max_chapters 章），返回最终状态。

        - 卡不存在 / card.json 损坏 → 直接 raise（不会静默新建，防丢已锻章计数）。
        - 已锻满：补评级定格（覆盖"上次评级前中断"的恢复路径）后返回，不再跑章。
        - 某章失败：卡标 error、上抛 ForgeChapterError；已成立的章保留，可再次调用续锻。
        - 限章截断（max_chapters）：卡标 paused（明确"没在跑了"），续锻无损。
        """
        state = load_card_state(self._gacha_root, card_id)
        if state is None:
            # 也发一条 error 事件：SSE 端的 _produce 把 ForgeChapterError 视为"runner 已自报"
            # 而静默收尾——这条不发，客户端只会看到流无声结束、误以为锻造完成。
            await self._emit(
                on_event,
                {
                    "type": "error",
                    "card_id": card_id,
                    "chapter": 0,
                    "message": f"卡不存在或 card.json 损坏：{card_id}",
                },
            )
            raise ForgeChapterError(f"卡不存在或 card.json 损坏：{card_id}")

        if not state.can_advance():
            if state.status != "complete":
                await self._grade_and_complete(state, on_event)
            await self._emit_done(state, on_event)
            return state

        state.status = "forging"
        save_card_state(self._gacha_root, state)
        ran = 0
        try:
            while state.can_advance() and (max_chapters is None or ran < max_chapters):
                await self._run_one_chapter(state, on_event)
                ran += 1
        except ForgeChapterError as exc:
            state.status = "error"
            save_card_state(self._gacha_root, state)
            await self._emit(
                on_event,
                {
                    "type": "error",
                    "card_id": state.card_id,
                    "chapter": state.current_chapter + 1,
                    "message": str(exc),
                },
            )
            raise

        if state.can_advance():
            # 限章截断：没锻满但也没在跑了——标 paused，续锻从断点继续
            state.status = "paused"
            save_card_state(self._gacha_root, state)
        else:
            await self._grade_and_complete(state, on_event)
        await self._emit_done(state, on_event)
        return state

    # ────────── 单章 ──────────

    async def _run_one_chapter(self, state: CardState, on_event: OnForgeEvent | None) -> None:
        """锻一章：phase-1 传记+人格+推进落盘（必出，失败 raise）→ phase-2 装 skill（best-effort）。"""
        next_no = state.current_chapter + 1
        age_range = state.next_age_range()
        await self._emit(
            on_event,
            {
                "type": "chapter_start",
                "card_id": state.card_id,
                "chapter": next_no,
                "end_chapter": state.end_chapter,
                "age_range": age_range,
            },
        )

        # ── phase-1（零工具、必出）──
        prompt = self._build_prompt(state, next_no, age_range)
        session = Session.new(channel=_GACHA_CHANNEL, user_id=_GACHA_USER_ID)
        _logger.info(
            "锻造 phase-1（传记，零工具）开始",
            card_id=state.card_id,
            forge_session=session.session_id,
            chapter=next_no,
            age_range=age_range,
            prompt_chars=len(prompt),
        )
        try:
            result = await self._engine.complete_turn(
                session, prompt, max_turns=_PHASE1_MAX_TURNS, use_tools=False
            )
        except Exception as exc:
            _logger.error(
                "锻造 phase-1 complete_turn 失败（不推进状态）",
                card_id=state.card_id,
                error=str(exc),
                forge_session=session.session_id,
            )
            raise ForgeChapterError(f"engine.complete_turn 失败：{exc}") from exc

        raw_text = result.content if isinstance(result.content, str) else ""

        # 触顶时 content 是固定文案，没有真内容可修——直接硬失败，不喂给 JSON 修复。
        if raw_text.strip() == TOOL_TURN_LIMIT_MESSAGE:
            raise ForgeChapterError(
                f"第 {next_no} 章 tool 调用触顶（上限 {_PHASE1_MAX_TURNS} 轮），未产出结果"
            )

        parsed = parse_structured_output(raw_text)
        if parsed is None:
            parsed = await self._repair_structured_output(raw_text, session.session_id)
        if parsed is None:
            _logger.error(
                "锻造输出 JSON 修复后仍不合法（不推进状态）",
                card_id=state.card_id,
                chapter=next_no,
                raw_preview=raw_text[:200],
            )
            raise ForgeChapterError(f"第 {next_no} 章结构化输出无法解析（一次修复后仍畸形）")

        coerced = _coerce_chapter_payload(parsed)  # narrative 空会 raise ForgeChapterError

        narrative: str = coerced["narrative"]
        # age_range 规范化（唯一收口点）：LLM 常回带"岁"/年代注的装饰串（"10-15岁（2002-2007）"），
        # 存进 card.json 会让每个下游消费者各自打"岁 岁"补丁——只认干净的 "lo-hi"，否则用状态算值。
        out_age_range = coerced.get("age_range")
        if not (
            isinstance(out_age_range, str)
            and re.fullmatch(r"\d{1,3}-\d{1,3}", out_age_range.strip())
        ):
            out_age_range = age_range
        else:
            out_age_range = out_age_range.strip()

        try:
            self._write_biography(state, next_no, out_age_range, narrative, coerced)
        except Exception as exc:
            _logger.error(
                "锻造传记落盘失败（不推进状态）",
                card_id=state.card_id,
                error=str(exc),
                chapter=next_no,
            )
            raise ForgeChapterError(f"第 {next_no} 章传记落盘失败：{exc}") from exc

        # 人格整体演化（best-effort）：失败只记日志，不影响传记 + 状态推进
        prev_active_chapter = state.active_persona_chapter
        persona_ok = self._evolve_persona(state, next_no, prev_active_chapter, coerced)

        report = _coerce_report(coerced.get("report"), fallback=narrative)
        record = ChapterRecord(
            age_range=out_age_range,
            summary=narrative,
            report=report,
            installed_skills=[],  # phase-2 装好后回填
        )
        state.advance(record)
        if not persona_ok:
            # 本章人格目录没写成：激活指针留在上一个真实存在的章——否则指针指向空目录，
            # 转生被拒、下一章又从空目录拷贝起步，把 _protocol.md 和未演化段落全弄丢。
            state.active_persona_chapter = prev_active_chapter
        save_card_state(self._gacha_root, state)
        _logger.info(
            "锻造 phase-1 完成，状态已推进（安装前已落地，绝不被 phase-2 回退）",
            card_id=state.card_id,
            chapter=state.current_chapter,
            age=state.age,
            active_persona_chapter=state.active_persona_chapter,
        )
        await self._emit(
            on_event,
            {
                "type": "chapter_done",
                "card_id": state.card_id,
                "chapter": state.current_chapter,
                "end_chapter": state.end_chapter,
                "age": state.age,
                "age_range": out_age_range,
                "report": report,
                # 注意：不带 narrative 全文——SSE 别为没人读的字段每章多扛几 KB；
                # 全文走 GET /api/gacha/cards/{id}/chapters/{n}
            },
        )

        # ── phase-2（best-effort、bounded）：状态已推进，失败绝不回退已成立的章 ──
        installed = await self._install_chapter_skills(state, next_no, coerced, session.session_id)
        if installed:
            state.chapters[-1].installed_skills = installed
            save_card_state(self._gacha_root, state)
            await self._emit(
                on_event,
                {
                    "type": "skill_installed",
                    "card_id": state.card_id,
                    "chapter": next_no,
                    "skills": installed,
                },
            )

    async def _install_chapter_skills(
        self,
        state: CardState,
        chapter_no: int,
        coerced: dict[str, Any],
        session_id: str,
    ) -> list[str]:
        """phase-2 入口：派生意图 → 按每卡剩余预算装；无安装器/无意图/预算耗尽都直接 []。"""
        if self._installer is None:
            return []
        intents = derive_skill_install_intents(coerced)
        if not intents:
            return []
        budget = self._skills_per_card_cap - state.installed_skill_count()
        if budget <= 0:
            _logger.info(
                "锻造 phase-2 跳过（本卡安装预算已用尽）",
                card_id=state.card_id,
                chapter=chapter_no,
                cap=self._skills_per_card_cap,
            )
            return []
        return await self._installer.install_for_chapter(
            chapter_no=chapter_no,
            intents=intents,
            session_id=session_id,
            max_installs=budget,
        )

    # ────────── 评级定格 ──────────

    async def _grade_and_complete(self, state: CardState, on_event: OnForgeEvent | None) -> None:
        """锻满后评级 + 命名（best-effort）并定格为 complete。评级失败不否定卡本身。"""
        session = Session.new(channel=_GACHA_CHANNEL, user_id=_GACHA_USER_ID)
        rarity, title = await grade_card(self._engine.llm, state, session_id=session.session_id)
        if rarity.grade:
            state.rarity = rarity
        # 不覆盖已有卡名（创始卡"三十六贱笑·本源"这类预命名优先）
        if title and not state.title:
            state.title = title
        state.status = "complete"
        save_card_state(self._gacha_root, state)
        _logger.info(
            "卡已定格",
            card_id=state.card_id,
            title=state.title,
            grade=state.rarity.grade or "未评级",
            score=state.rarity.score,
            age=state.age,
        )
        await self._emit(
            on_event,
            {
                "type": "rarity",
                "card_id": state.card_id,
                "grade": state.rarity.grade,
                "score": state.rarity.score,
                "comment": state.rarity.comment,
                "title": state.title,
            },
        )

    # ────────── prompt ──────────

    def _build_prompt(self, state: CardState, chapter_no: int, age_range: str) -> str:
        """拼锻造 prompt：命运种子常驻 + 累积传记前置 + 本章年代锚 + 内联输出协议。

        phase-1 无工具，LLM 拉不到 Skill(gacha) 正文——所以协议要点必须全部内联在这里
        （skills/gacha/SKILL.md 是同一协议的文档版，供 listing 与人读）。
        """
        seed = state.seed
        lo = state.start_age + state.current_chapter * state.years_per_chapter
        hi = lo + state.years_per_chapter
        year_lo = seed.birth_year + lo
        year_hi = seed.birth_year + hi
        lines: list[str] = [
            f"现在是你的第 {chapter_no} 次锻造梦"
            f"（这张人生卡共 {state.end_chapter} 章，本章覆盖 {age_range} 岁，约公历 {year_lo}-{year_hi} 年）。",
            "你是三十六贱笑在平行世界的一个分身：人格底色（爱搞笑、嘴贱、一双看穿生活荒诞的眼睛）"
            "继承本体，但这一世的出身、环境、际遇**以下面的命运种子为准**——种子与本体设定冲突时，"
            "一律以种子为准。",
            "",
            "**这张卡的命运种子（每章常驻，不可违背）：**",
            f"- 世界类型：{seed.genre_label}。这是整张卡人生的主基调——写实类型就全程贴现实；"
            "幻想类型则在合适时点转入该类型的世界观，转入前的童年照常写实。",
            f"- 出身：{seed.origin or '由你按世界类型自定（定了就全程延续）'}",
            f"- 天赋：{'、'.join(seed.talents) if seed.talents else '无特别天赋（全靠后天）'}",
        ]
        if seed.trigger:
            lines.append(
                f"- 命运触发事件：「{seed.trigger}」。不必发生在第一章——由你在少年期到成年早期的"
                "合适时点引爆；一旦引爆，后续各章都要承接它带来的世界线变化，不许当没发生过。"
            )
        lines.append(
            f"- 创意度：{seed.creativity:.1f}/2.0，{_creativity_clause(seed.creativity)}。"
        )
        if seed.custom_prompt:
            lines.append(f"- 主人补充设定：{seed.custom_prompt}")
        lines.append("")
        lines.append(
            "把本章这段时间写得**具体到名字**——事件/场景/对话/细节要落地，涉及的地点、学校、专业、"
            "机构、作品、人名都写出具体名字（“考上大学”要写成“考入 ××大学 ××专业”）。"
            "**这一章是上一章的直接延续，不是重开一个新设定**：从上一章结尾的处境/身份/所在世界往后接着写。"
            "天马行空体现在“既定这条线能走到多远多奇”，不是每章换一个开头；延续的前提下尽管大胆——"
            "命运急转、奇遇、把伏笔养大，宁可惊人也别写成平淡流水账。"
            "两条底线：①写实向的剧情要对得上现实、贴合本章年代"
            f"（约公历 {year_lo}-{year_hi} 年，真实的大学要真有那个专业、别造假机构），**但剧情若已转向非写实"
            "（穿越/异世界/修仙），就以故事世界的内在时间线为准、公历年代只作参考，别为贴年代把人物硬拉回现代**；"
            "②无论多放飞都要自洽、圆得回来、显式承接前文，不是跳到无关的新设定。",
        )
        lines.append("")
        if state.chapters:
            lines.append(
                "**以下是这张卡已经历的传记（前几章），本章必须从最后一章的结尾接着往后长、"
                "延续既定的世界线与身份，不是另起炉灶：**"
            )
            lines.append("")
            for i, ch in enumerate(state.chapters, 1):
                # 新章 age_range 已在落盘前规范成 "lo-hi"；老数据（迁移/规范化之前锻的）可能带"岁"
                age_label = ch.age_range if "岁" in ch.age_range else f"{ch.age_range} 岁"
                lines.append(f"==== 第 {i} 章 · {age_label} ====")
                lines.append(ch.summary.strip())
                lines.append("")
        else:
            lines.append(
                f"这是这张卡的第一章（{state.start_age} 岁起点）。从命运种子定下的出身环境开始往后长——"
                "出生底色是三十六贱笑（爱搞笑的小孩），但生在哪、长在哪、遇见什么，听种子的。"
            )
            lines.append("")
        lines.append(
            "**重要**：不要调 LoadMemory / SaveMemory——这张卡的传记已全部前置在上面，"
            "传记落盘也由系统代码确定性完成，你只负责产出本章内容。"
        )
        lines.append(
            "最后，只输出一个结构化 JSON 对象（含 narrative / age_range / learned / "
            "personality / report / skill_intents / persona），不要在 JSON 之外写任何多余文字。"
        )
        lines.append(
            "字段形状必须严格遵守：learned 是字符串数组；skill_intents 是对象数组，"
            '每项只用 {"domain": "短领域词", "why": "原因"}，不要写成 skill/reason、'
            "intent_name/reason、编号字符串或对象字典。**domain 要写成公开 skill 库搜得到的通用大类词、"
            "优先英文**（如 “comedy writing”、“public speaking”、“storytelling”、“martial arts”、"
            "“investing”），别用自造的窄中文短语（“双人喜剧配合”“预设陷阱式编剧”那种几乎搜不到、装不上）；"
            "宁可宽一点、可检索，也别精确但没人收录。"
        )
        lines.append(
            "其中 report 是这一章面向主人看的**锻造汇报**（卡册会展示给主人，"
            "用第一人称、口语化地讲清楚你这一章长成了谁、有什么变化）。"
        )
        lines.append(
            "其中 persona 是本章演化后的**新核心人格**（{identity, personality, beliefs, style, "
            "fewshot_short} 各为一整段 markdown，写你这岁数已经长成的那个人）——给了哪段就**整段**"
            "成为这张卡往后的真身（长成剑修就只剩剑修，不留旧身份残影）。载体协议（≤60字、<MSG>、禁 "
            "markdown 等）和安全红线另有 _protocol.md 永驻层兜底，你**不用写、也不要复写**进 persona。"
            "**本章必须演化人格，不允许把 persona 整个省略**：至少给 identity"
            "（“我现在是谁”每章都在变），再加上本章经历真正影响到的段落（口吻变了就给 style、"
            "价值观变了就给 beliefs、性格变了就给 personality、典型样例变了就给 fewshot_short）；"
            "只有确实毫无变化的段落才省略（省略的会自动承接前章）。"
        )
        lines.append(
            "**persona 每段都要写成规整的 markdown 文档**，不是一坨大白话：以 `#` 标题起头（如 "
            "`# 核心身份 · 我是谁`），再按内容用 `##` 分小节、合适处用列表/引用——像基础人格文件那样有层次。"
            "各段会按 `---` 拼进 system prompt，没标题没结构就糊成一团、分不清哪段是身份哪段是口吻。"
        )
        lines.append(
            "注意区分两个同名字段：顶层 personality 是**一句话摘要**（写进传记回看），"
            "persona.personality 是**整段性格 markdown**（整段覆盖性格人格文件）——两者都要给，"
            "别只填顶层那个就把 persona 整段漏掉。"
        )
        return "\n".join(lines)

    # ────────── phase-1 辅助 ──────────

    async def _repair_structured_output(self, raw: str, session_id: str) -> dict[str, Any] | None:
        """纯提取失败后调**一次** LLM 修复：把畸形输出重发成纯 JSON 对象，再解析。

        走 llm 简单 chat（无工具、不再走 complete_turn，避免又一个 tool 循环烧预算）。
        修复也失败 / 仍非 dict → 返 None，调用方据此硬失败（不静默降级）。
        """
        instruction = (
            "下面是一段本应只含单个 JSON 对象、但被写坏了的锻造输出"
            "（可能有未闭合括号、尾逗号、或 JSON 前后夹了多余文字）。"
            "请**只**重新输出那个 JSON 对象本身，不要任何解释、不要 markdown 代码围栏，"
            "保留原有字段（narrative / age_range / learned / personality / report / "
            "skill_intents / persona），缺的字段不要编造。\n\n"
            f"坏输出：\n{raw}"
        )
        try:
            result = await self._engine.llm.chat(
                messages=[{"role": "user", "content": instruction}],
                session_id=session_id,
                channel=_GACHA_CHANNEL,
                user_id=_GACHA_USER_ID,
            )
        except Exception as exc:
            _logger.error("锻造 JSON 修复调用失败", error=str(exc), session_id=session_id)
            return None
        repaired = parse_structured_output(result.text)
        if repaired is None:
            _logger.warning(
                "锻造 JSON 修复后仍无法解析",
                session_id=session_id,
                repaired_preview=result.text[:200],
            )
        return repaired

    def _write_biography(
        self,
        state: CardState,
        chapter_no: int,
        age_range: str,
        narrative: str,
        parsed: dict[str, Any],
    ) -> Path:
        """把本章传记确定性写进卡目录 biography/chapter-N.md（**不写 memdir**）；附习得/人格摘要。"""
        bio_dir = biography_dir(self._gacha_root, state.card_id)
        bio_dir.mkdir(parents=True, exist_ok=True)
        learned = parsed.get("learned")
        personality = parsed.get("personality")
        # LLM 偶尔回带"岁"或年代注的 age_range（如"5-10岁（约1997-2002年）"），别再拼出"岁 岁"
        age_label = age_range if "岁" in age_range else f"{age_range} 岁"
        body_parts = [
            f"# 第 {chapter_no} 章 · {age_label}",
            narrative.strip() or "（本章叙述为空）",
        ]
        if isinstance(learned, list) and learned:
            learned_lines = "\n".join(f"- {item}" for item in learned if isinstance(item, str))
            if learned_lines:
                body_parts.append("**本章习得：**\n" + learned_lines)
        if isinstance(personality, str) and personality.strip():
            body_parts.append("**人格演化：**\n" + personality.strip())
        path = biography_path(self._gacha_root, state.card_id, chapter_no)
        path.write_text("\n\n".join(body_parts) + "\n", encoding="utf-8")
        return path

    def _evolve_persona(
        self, state: CardState, chapter_no: int, prev_active_chapter: int, parsed: dict[str, Any]
    ) -> bool:
        """整体演化卡人格：首章前快照出生底版 chapter-0，再把本章演化段落写进 chapter-N。

        best-effort：任一步失败只记日志、返回 False，不影响传记 + 状态推进——调用方据此把
        激活指针留在上一个真实存在的章（防指向空目录）。协议层每章从 base core 重拷
        （protocol_src），主人改红线后新锻的章立刻跟上。不触碰 PersonaLoader——卡锻造不改
        本体当前人格（转生才改）。
        """
        try:
            proot = persona_root(self._gacha_root, state.card_id)
            snapshot_base_core_to_chapter0(self._persona_dir, proot)
            sections = filter_persona_sections(parsed.get("persona"))
            write_chapter_persona(
                persona_root=proot,
                chapter_no=chapter_no,
                prev_chapter_no=prev_active_chapter,
                persona_sections=sections,
                protocol_src=self._persona_dir / CORE_DIRNAME / PROTOCOL_FILENAME,
            )
            return True
        except Exception as exc:
            _logger.error(
                "卡人格演化失败（跳过，激活指针留在上一章）",
                error=str(exc),
                card_id=state.card_id,
                chapter=chapter_no,
            )
            return False

    # ────────── 事件 ──────────

    async def _emit(self, on_event: OnForgeEvent | None, event: dict[str, Any]) -> None:
        """发一条锻造事件；回调异常只记日志（观测通道绝不拖垮锻造本身）。"""
        if on_event is None:
            return
        try:
            await on_event(event)
        except Exception as exc:
            _logger.warning(
                "锻造事件回调异常（忽略，不影响锻造）",
                error=str(exc),
                event_type=event.get("type"),
            )

    async def _emit_done(self, state: CardState, on_event: OnForgeEvent | None) -> None:
        await self._emit(
            on_event,
            {
                "type": "done",
                "card_id": state.card_id,
                "status": state.status,
                "title": state.title,
                "age": state.age,
                "chapters": state.current_chapter,
                "end_chapter": state.end_chapter,
                "grade": state.rarity.grade,
            },
        )


# ────────── 模块级辅助（schema 校验 / 创意度档位） ──────────


def _creativity_clause(creativity: float) -> str:
    """创意度 → prompt 措辞档位（保守 0 ↔ 2 狂野）；engine 不暴露温度，措辞是 v1 唯一杠杆。"""
    if creativity < 0.7:
        return "保守档：以写实为主，奇遇克制、命运转折要有充分铺垫，宁稳不怪"
    if creativity <= 1.3:
        return "平衡档：写实打底，允许大胆的命运急转与奇遇，放飞但圆得回来"
    return (
        "狂野档：尽管天马行空——穿越/修仙/异能/位面跳跃可以更早更猛地上，"
        "但仍要自洽承接、显式接住前文"
    )


def _coerce_chapter_payload(parsed: dict[str, Any]) -> dict[str, Any]:
    """schema 校验/兜底：解析出的 dict 仍可能字段缺失/类型错；规整成可安全落盘的形状。

    - narrative：**强制非空字符串**，否则本章没有真内容可落盘 → raise ForgeChapterError。
    - learned / skill_intents：兼容常见 LLM 变体并规整成 list；无法拆成明确条目的再置 []。
    - persona：非 dict → {}（filter_persona_sections 据此安全跳过演化）。
    - personality：非 str → ""。
    - age_range / report：保持原值，由调用方现有的回落逻辑兜底。
    返回浅拷贝（不就地改 parsed，便于排查）。
    """
    narrative = parsed.get("narrative")
    if not isinstance(narrative, str) or not narrative.strip():
        raise ForgeChapterError("结构化输出缺少非空 narrative")

    out: dict[str, Any] = dict(parsed)
    out["narrative"] = narrative
    out["learned"] = coerce_learned_items(parsed.get("learned"))
    out["skill_intents"] = coerce_skill_intents(parsed.get("skill_intents"))
    persona = parsed.get("persona")
    out["persona"] = persona if isinstance(persona, dict) else {}
    personality = parsed.get("personality")
    out["personality"] = personality if isinstance(personality, str) else ""
    return out


def _coerce_report(raw: object, *, fallback: str) -> str:
    """把 report 字段规整成非空字符串；缺失 / 空 / 非串则回落 fallback（本章 narrative）。"""
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return fallback.strip()
