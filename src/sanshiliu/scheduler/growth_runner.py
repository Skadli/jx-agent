"""成长执行器；闸门通过后被调用，让 engine 跑一章"成长梦"并落地传记 + 推进状态。

设计要点（平移自 dream_runner.py，但产物不同）：
- **累积传记前置注入**：把前几章的传记摘要拼进 user_text，让本章逻辑自洽地承接前文，
  而不是靠 LLM 自己调 LoadMemory（合成 session 的 channel=growth 没有历史可读）。
- **合成 session**：每章新建 Session.new(channel="growth", user_id="growth")，不污染真实
  用户会话历史；成长本身也会写一条 sessions 表记录，dashboard 可追溯。
- **结构化输出 + 修复重试 + 如实上报**：成长协议强制 LLM 输出单个 JSON `{narrative, age_range,
  learned, personality, skill_intents}`。纯提取失败（无 JSON / 畸形）→ 调**一次** LLM 修复重发
  纯 JSON；再做 schema 校验（narrative 强制非空、数组/对象字段兜底）。修复后仍不合法 / tool 触顶 /
  传记落盘失败 → raise GrowthChapterError，**不静默降级**，由 __call__ 上抛给 heartbeat 标 error。
- **确定性落盘**：传记由代码用 write_memory_file 写 `reference_growth-chapter-N.md`
  （name 满足 SaveMemory 的合规字符集，仅字母数字与连字符下划线），不依赖 LLM 自己调
  SaveMemory（合成 session 里工具是否可靠不保证；落盘必须确定）。
- **三态如实上报、但不崩 tick**：已定格 = 合法 no-op（正常返回，标 ok）；已推进 = 正常返回
  （标 ok，last_message 带"第 N 章已完成"）；降级/致命失败 = 上抛 GrowthChapterError，heartbeat
  ._execute 标 last_status="error" 且 catch 住不崩 tick。不再把致命降级吞成 ok（旧 bug：state 空转）。

PR3 技能习得（落实 #2/#5）：成长协议指示 LLM 对每个知识缺口**主动调 Skill(skill-finder)**
查找并安装真实 skill（绝不自造 SKILL.md）。安装在 complete_turn 的 tool 循环里有机发生——
runner 不写安装逻辑，只做两件事：
1. **无人值守自动放行**：complete_turn 全程用 growth_approvals 的 contextvar 圈一个窗口，
   让 Skill / installer 的 bash 等工具调用免审批通过（CompositeConfirmer 据此路由）。
   窗口严格只覆盖这一次 complete_turn，finally 必复位（不外溢到别的请求）。
2. **目录 diff 记账**：complete_turn 前快照 skill_loader.list() 的 id 集合；跑完
   invalidate()+reload 再 diff，**新增的 id = 本章真正装上的 skill**（目录是真相源，不只信
   LLM 自报）。记入 ChapterRecord.installed_skills 并随状态落盘。找不到→无新增→空列表、不报错。

PR2 已实现：跑一章 → 写传记 → **整体演化人格（版本化覆盖 base core）** → 推进状态。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sanshiliu.engine.loop import TOOL_TURN_LIMIT_MESSAGE
from sanshiliu.engine.session import Session
from sanshiliu.foundation.logging import get_logger
from sanshiliu.memory.longterm.memdir import write_memory_file
from sanshiliu.memory.types import MemoryEntry
from sanshiliu.scheduler.growth_persona import (
    filter_persona_sections,
    snapshot_base_core_to_chapter0,
    write_chapter_persona,
)
from sanshiliu.scheduler.growth_state import (
    ChapterRecord,
    GrowthState,
    load_growth_state,
    save_growth_state,
)
from sanshiliu.security.growth_approvals import (
    enter_growth_autoallow,
    exit_growth_autoallow,
)

if TYPE_CHECKING:
    from sanshiliu.engine.loop import ConversationEngine
    from sanshiliu.identity.loader import PersonaLoader
    from sanshiliu.skills.loader import SkillLoader

_logger = get_logger(__name__)

_GROWTH_CHANNEL = "growth"
_GROWTH_USER_ID = "growth"

# 成长一章 tool 预算：load growth + skill-finder + 搜索 + 查 npx + 装 + 最后一轮输出 JSON，
# 6 轮（默认）不够；给足余量但仍有上限（防失控烧钱）。触顶仍会返回 TOOL_TURN_LIMIT_MESSAGE。
_GROWTH_MAX_TURNS = 16

# 从 LLM 输出里抠 JSON：优先 ```json fenced``` 块，其次裸 {...}。成长协议要求只输出一个 JSON。
_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class GrowthChapterError(RuntimeError):
    """本章成长失败的典型错误（区别于"已定格"这种正常 no-op）。

    由 _run_one_chapter 在以下致命场景 raise，并经 __call__ 上抛给 heartbeat._execute
    标 last_status="error"（如实上报、不静默降级）：tool 触顶、JSON 修复后仍不合法、
    narrative 为空、传记落盘失败。__call__ 据此把"降级/失败"与"已定格/已推进"区分开。
    """


class GrowthRunner:
    """读状态 → 拼 prompt → 跑 engine 一章 → 解析结构化输出 → 写传记 → 推进状态。

    可直接当 OnDueCallback 用（__call__ 实现匹配 (ctx)->None 签名）。
    """

    def __init__(
        self,
        *,
        engine: ConversationEngine,
        growth_state_path: Path,
        memdir_dir: Path,
        start_age: int,
        years_per_chapter: int,
        end_age: int,
        persona_dir: Path | None = None,
        data_dir: Path | None = None,
        persona_loader: PersonaLoader | None = None,
        skill_loader: SkillLoader | None = None,
    ) -> None:
        self._engine = engine
        self._state_path = growth_state_path
        self._memdir_dir = memdir_dir
        self._start_age = start_age
        self._years_per_chapter = years_per_chapter
        self._end_age = end_age
        # PR2 人格演化所需：base core 来源（persona_dir/core）+ 版本化落盘根（data_dir/growth/persona）
        # + 写完热生效的 loader。三者缺任一则跳过人格演化（仍写传记 + 推进状态，不报错），
        # 这样单测 / 不传的调用点也能跑。
        self._persona_dir = persona_dir
        self._data_dir = data_dir
        self._persona_loader = persona_loader
        # PR3 技能习得所需：同一个 SkillLoader 实例，用于"装前/装后目录 diff"记账 + reload 热生效。
        # 缺它（单测/不传）则跳过 skill 记账（installed_skills 留空），不影响传记/人格/状态推进。
        self._skill_loader = skill_loader

    async def __call__(self, ctx: dict[str, Any]) -> None:
        """OnDueCallback 签名；闸门已在 scheduler 侧判过，这里再读一次状态防并发竞态。

        三态如实上报（#1）：
        - 已定格 / 已推进（正常）→ 正常返回，并把人话结果写进 ctx["result_message"]，
          heartbeat._execute 据此标 last_status="ok" + last_message=真实结果（区分"第 N 章已完成"
          与"已定格"，而非笼统"完成"）。
        - 降级 / 致命失败（GrowthChapterError）→ **上抛**给 heartbeat._execute，它会标
          last_status="error" + last_message=真因，且 catch 住不崩 tick。绝不再静默吞成 ok。
        contextvar 的自动放行窗口由 _run_one_chapter 内部 finally 复位，这里上抛不会泄漏它。
        """
        result_message = await self._run_one_chapter()
        # 成功（已推进/已定格）才会走到这——把人话结果交给 heartbeat 当 last_message。
        ctx["result_message"] = result_message

    async def _run_one_chapter(self) -> str:
        state = load_growth_state(
            self._state_path,
            start_age=self._start_age,
            years_per_chapter=self._years_per_chapter,
            end_age=self._end_age,
        )
        if not state.can_advance():
            # 已定格：合法 no-op（不是失败）。正常返回人话结果给 heartbeat 标 ok。
            _logger.info(
                "成长已定格，跳过", current_chapter=state.current_chapter, end=state.end_chapter
            )
            return f"已定格（满 {state.end_chapter} 章 / {self._end_age} 岁），不再推进"

        next_chapter_no = state.current_chapter + 1
        age_range = state.next_age_range()
        prompt = self._build_prompt(state, next_chapter_no, age_range)
        session = Session.new(channel=_GROWTH_CHANNEL, user_id=_GROWTH_USER_ID)
        # PR3：装前快照已加载的 skill id 集合——complete_turn 跑完再 diff，新增的就是本章装上的。
        skills_before = self._snapshot_skill_ids()
        _logger.info(
            "成长执行器开始执行",
            growth_session=session.session_id,
            chapter=next_chapter_no,
            age_range=age_range,
            prompt_chars=len(prompt),
            skills_before=len(skills_before),
        )
        # PR3：成长无人值守自动放行窗口——complete_turn 里 LLM 调 Skill(skill-finder)/installer
        # 及其 bash 子调用走 ask 路径时，CompositeConfirmer 据此 contextvar 免审批放行（#5）。
        # 窗口严格只包住这一次 complete_turn；finally 必复位，绝不外溢到别的请求。
        # 总开关仍是 growth_enabled=false（心跳任务不 enabled 则整条成长线含本放行都不跑）。
        # #4：成长一章步骤多，默认 6 轮不够，显式抬高 tool 预算。
        token = enter_growth_autoallow()
        try:
            result = await self._engine.complete_turn(
                session, prompt, max_turns=_GROWTH_MAX_TURNS
            )
        except Exception as exc:
            # #1：致命失败必须如实上抛给 heartbeat（标 error），不再静默吞成 ok。
            # 但安装可能已在 complete_turn 异常前发生过——#2 要求无条件记账，故先 diff 再 raise。
            self._collect_installed_skills(skills_before, next_chapter_no, advanced=False)
            _logger.error(
                "engine.complete_turn 失败（不推进状态，上报 error）",
                error=str(exc),
                growth_session=session.session_id,
            )
            raise GrowthChapterError(f"engine.complete_turn 失败：{exc}") from exc
        finally:
            exit_growth_autoallow(token)

        # #2：技能记账无条件先跑——安装发生在 complete_turn 的 tool 循环里（先于解析），
        # 不论后续解析成功/降级，本章目录 diff 出的已装 skill 都要被审计到，绝不漏记/静默。
        # advanced 仅决定日志措辞（成功记入 ChapterRecord；降级则记为"已装未推进"以便排查）。
        raw_text = result.content if isinstance(result.content, str) else ""

        # #4：触顶时 content 是固定文案，没有真内容可修——直接当硬失败上抛，不喂给 JSON 修复。
        if raw_text.strip() == TOOL_TURN_LIMIT_MESSAGE:
            self._collect_installed_skills(skills_before, next_chapter_no, advanced=False)
            _logger.error(
                "成长 tool 调用触顶（不推进状态，上报 error）",
                growth_session=session.session_id,
                chapter=next_chapter_no,
                max_turns=_GROWTH_MAX_TURNS,
            )
            raise GrowthChapterError(
                f"第 {next_chapter_no} 章 tool 调用触顶（上限 {_GROWTH_MAX_TURNS} 轮），未产出结果"
            )

        # #3：先走纯提取；失败（畸形/无 JSON、但有真内容）→ 一次 LLM 修复重发 → 再解析。
        parsed = _parse_structured_output(raw_text)
        if parsed is None:
            parsed = await self._repair_structured_output(raw_text, session.session_id)
        if parsed is None:
            self._collect_installed_skills(skills_before, next_chapter_no, advanced=False)
            _logger.error(
                "成长输出 JSON 修复后仍不合法（不推进状态，上报 error）",
                growth_session=session.session_id,
                chapter=next_chapter_no,
                raw_preview=raw_text[:200],
            )
            raise GrowthChapterError(
                f"第 {next_chapter_no} 章结构化输出无法解析（一次修复后仍畸形）"
            )

        # #3：schema 校验/兜底——narrative 强制非空（空则硬失败），其余字段缺失/类型错则兜底。
        try:
            coerced = _coerce_chapter_payload(parsed)
        except GrowthChapterError:
            self._collect_installed_skills(skills_before, next_chapter_no, advanced=False)
            _logger.error(
                "成长输出 narrative 为空（不推进状态，上报 error）",
                growth_session=session.session_id,
                chapter=next_chapter_no,
            )
            raise

        narrative = coerced["narrative"]
        # 优先用 LLM 回的 age_range，缺失/非串则用状态算出的（确保传记标题不空）
        out_age_range = coerced.get("age_range")
        if not isinstance(out_age_range, str) or not out_age_range.strip():
            out_age_range = age_range

        # 确定性落盘：传记写 reference_growth-chapter-N.md（name 合规正则 [A-Za-z0-9_\-]{5,40}）
        try:
            self._write_biography(next_chapter_no, out_age_range, narrative, coerced)
        except Exception as exc:
            # 落盘失败 = 本章产物没真正落地，算降级；先无条件记账已装 skill 再上抛。
            self._collect_installed_skills(skills_before, next_chapter_no, advanced=False)
            _logger.error(
                "成长传记落盘失败（不推进状态，上报 error）",
                error=str(exc),
                chapter=next_chapter_no,
            )
            raise GrowthChapterError(f"第 {next_chapter_no} 章传记落盘失败：{exc}") from exc

        # PR2 人格整体演化：先把本章演化人格写进版本化目录 data/growth/persona/chapter-N/
        #     （base persona/core 全程不写），再推进 state（advance 把 active_persona_chapter
        #     指到本章 N），最后 invalidate 让 PersonaLoader 下次 get 读到新人格——日常对话即
        #     以"长成的人"回应。任一步失败只记日志、跳过演化，**不影响传记 + 状态推进**。
        self._evolve_persona(next_chapter_no, state.active_persona_chapter, coerced)

        # #2 技能习得记账（推进成功路径）：装好的 skill 落成 skills/<id>/，complete_turn 里已由
        # LLM 经 Skill(skill-finder)/installer 有机安装。这里 invalidate+reload 后 diff 目录，
        # 新增 id = 本章真正装上的（目录是真相源，不只信 LLM 自报）。带 source 标记便于 dashboard 追溯。
        installed = self._collect_installed_skills(skills_before, next_chapter_no, advanced=True)

        # R8 每日汇报：dashboard 是汇报展示面（主动推送出 scope）。report 是 LLM 给的"面向人看"的
        # 当天成长汇报；缺失 / 非串则回落到 narrative，确保 dashboard 这一章总有汇报可看、不空白。
        report = _coerce_report(coerced.get("report"), fallback=narrative)
        record = ChapterRecord(
            age_range=out_age_range,
            summary=narrative,
            report=report,
            installed_skills=installed,
        )
        state.advance(record)
        save_growth_state(self._state_path, state)
        # advance 后 active_persona_chapter 已指向本章 N；让 loader 热生效到新人格
        if self._persona_loader is not None:
            self._persona_loader.invalidate()
        _logger.info(
            "成长执行器完成，状态已推进",
            growth_session=session.session_id,
            chapter=state.current_chapter,
            age=state.age,
            active_persona_chapter=state.active_persona_chapter,
            installed_skills=installed,
        )
        skill_tail = f"，装了 {len(installed)} 个 skill" if installed else ""
        return f"第 {state.current_chapter} 章已完成（{out_age_range} 岁，{state.age} 岁）{skill_tail}"

    def _snapshot_skill_ids(self) -> set[str]:
        """装前快照当前已加载 skill 的 id 集合；无 skill_loader（单测/未启用）则空集。

        用 list()（走缓存，不强制 reload）拿基线即可——本章新装的会在跑完 reload 后冒出来。
        """
        if self._skill_loader is None:
            return set()
        try:
            return {s.id for s in self._skill_loader.list()}
        except Exception as exc:
            # 记账是附加能力，读失败不能拖垮传记/状态推进
            _logger.warning("成长 skill 装前快照失败（记账降级为空）", error=str(exc))
            return set()

    def _collect_installed_skills(
        self, before: set[str], chapter_no: int, *, advanced: bool
    ) -> list[str]:
        """invalidate+reload 后 diff skill 目录，返回本章新增的 skill id（带审计日志）。

        目录是真相源：只有真把 skills/<id>/SKILL.md 装进去、且能被 loader 解析的才算数；
        LLM 自报装了但目录没有的不计入。无 skill_loader → 直接空列表（不报错）。

        #2：本方法在 complete_turn 之后**无条件**被调用——安装发生在 tool 循环里（先于解析），
        故不论本章推进成功还是降级/失败，目录 diff 出的已装 skill 都要被审计到，绝不漏记。
        advanced=False（降级路径）时把它们记为"已装未推进"——审计日志已捕获，不会变成静默泄漏；
        skill 的卸载/回滚仍是二期，本方法只负责记账。
        """
        if self._skill_loader is None:
            return []
        try:
            # 让 installer 这章新写进 skills/<id>/ 的目录被重新扫描到（同时令后续对话也能看到）
            self._skill_loader.invalidate()
            after = {s.id for s in self._skill_loader.list()}
        except Exception as exc:
            _logger.warning(
                "成长 skill 装后 reload 失败（记账降级为空）", error=str(exc), chapter=chapter_no
            )
            return []
        new_ids = sorted(after - before)
        if new_ids and advanced:
            # 审计：本章自动装了哪些真实 skill（落实 #5 的最低防护之一；另有 tool_calls 表留痕）
            _logger.info(
                "成长本章自动安装 skill（免审批 #5）",
                chapter=chapter_no,
                installed=new_ids,
                source=f"growth-chapter-{chapter_no}",
            )
        elif new_ids:
            # #2：本章降级未推进，但 skill 已真装进目录——审计为"已装未推进"，不让它静默漏掉。
            _logger.warning(
                "成长本章降级未推进，但已安装 skill（记审计、不计入章产物）",
                chapter=chapter_no,
                installed=new_ids,
                source=f"growth-chapter-{chapter_no}",
            )
        else:
            _logger.info(
                "成长本章未安装任何 skill（找不到真实 skill 或无意图）", chapter=chapter_no
            )
        return new_ids

    async def _repair_structured_output(
        self, raw: str, session_id: str
    ) -> dict[str, Any] | None:
        """纯提取失败后调**一次** LLM 修复：把畸形输出重发成纯 JSON 对象，再解析。

        #3：故意走 engine 的 llm 简单 chat（无工具、不再走 complete_turn，避免又一个 tool 循环
        烧预算/再触顶）。修复也失败 / 仍非 dict → 返 None，调用方据此当硬失败上抛（不静默降级）。
        """
        instruction = (
            "下面是一段本应只含单个 JSON 对象、但被写坏了的成长输出"
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
                channel=_GROWTH_CHANNEL,
                user_id=_GROWTH_USER_ID,
            )
        except Exception as exc:
            _logger.error(
                "成长 JSON 修复调用失败", error=str(exc), session_id=session_id
            )
            return None
        repaired = _parse_structured_output(result.text)
        if repaired is None:
            _logger.warning(
                "成长 JSON 修复后仍无法解析", session_id=session_id, repaired_preview=result.text[:200]
            )
        return repaired

    def _evolve_persona(
        self, chapter_no: int, prev_active_chapter: int, parsed: dict[str, Any]
    ) -> None:
        """整体演化人格：首章先快照 chapter-0（起点），再把本章演化段落写进 chapter-N。

        连续性：本章从"上一个激活章"目录拷贝起步（首章= chapter-0 起点），只覆盖 LLM 这章
        给出的段落——没演化的段落自动承接前章，核心永不为空。base persona/core 不写。
        缺 persona_dir / data_dir（PR1 调用点 / 单测不传）则整体跳过，不报错。
        """
        if self._persona_dir is None or self._data_dir is None:
            return
        try:
            # 首章前快照 base core → chapter-0（幂等；= 5 岁起点 = 原三十六贱笑）
            snapshot_base_core_to_chapter0(self._persona_dir, self._data_dir)
            sections = filter_persona_sections(parsed.get("persona"))
            # 起步基线 = 上一个激活章（advance 前的 active_persona_chapter）；首章时为 0
            write_chapter_persona(
                data_dir=self._data_dir,
                chapter_no=chapter_no,
                prev_chapter_no=prev_active_chapter,
                persona_sections=sections,
            )
        except Exception as exc:
            # 人格演化失败不能拖垮传记 + 状态推进；记日志后照常往下走
            _logger.error(
                "人格整体演化失败（跳过，不影响传记/状态）", error=str(exc), chapter=chapter_no
            )

    def _build_prompt(self, state: GrowthState, chapter_no: int, age_range: str) -> str:
        """拼成长 prompt：前置注入累积传记 + 本章年龄段，引导 LLM 按 growth 协议产出 JSON。"""
        lines: list[str] = [
            f"现在是你的第 {chapter_no} 次成长梦（共 {state.end_chapter} 章，本章覆盖 {age_range} 岁）。",
            "请按 Skill(growth) 协议完整走六步——读 growth skill 正文，承接前文继续成长。",
            "",
        ]
        if state.chapters:
            lines.append(
                "**以下是你已经历的成长传记（前几章），本章必须逻辑自洽地承接它们继续发展：**"
            )
            lines.append("")
            for i, ch in enumerate(state.chapters, 1):
                lines.append(f"==== 第 {i} 章 · {ch.age_range} 岁 ====")
                lines.append(ch.summary.strip())
                lines.append("")
        else:
            lines.append("这是你的第一章成长（5 岁起点 = 原三十六贱笑）。从这里开始往后长。")
            lines.append("")
        lines.append(
            "**重要**：不要再调 LoadMemory 取历史——你的成长传记已经全部前置在上面了，直接据此续写。"
        )
        lines.append(
            "按协议最后一步，只输出一个结构化 JSON 对象（含 narrative / age_range / learned / "
            "personality / report / skill_intents / persona），不要在 JSON 之外写多余文字。"
        )
        lines.append(
            "其中 report 是这一章面向人看的**当天成长汇报**（dashboard 会展示给主人看，"
            "用第一人称、口语化地讲清楚你这五年长成了谁、有什么变化）。"
        )
        lines.append(
            "其中 persona 是本章演化后的**新核心人格**（{identity, personality, beliefs, style, "
            "fewshot_short} 各为可选的整段 markdown，写你这岁数已经长成的那个人）——它会真正覆盖"
            "成为分身往后的真身；只写本章有变化的段落，没变的可省略（会自动承接前章）。"
        )
        return "\n".join(lines)

    def _write_biography(
        self, chapter_no: int, age_range: str, narrative: str, parsed: dict[str, Any]
    ) -> Path:
        """把本章传记确定性写成 reference_growth-chapter-N.md；附习得/性格摘要便于回看。"""
        learned = parsed.get("learned")
        personality = parsed.get("personality")
        body_parts = [narrative.strip() or "（本章叙述为空）"]
        if isinstance(learned, list) and learned:
            learned_lines = "\n".join(f"- {item}" for item in learned if isinstance(item, str))
            if learned_lines:
                body_parts.append("**本章习得：**\n" + learned_lines)
        if isinstance(personality, str) and personality.strip():
            body_parts.append("**人格演化：**\n" + personality.strip())
        entry = MemoryEntry(
            name=f"growth-chapter-{chapter_no}",
            description=f"成长传记 第{chapter_no}章 - {age_range}岁",
            memory_type="reference",
            source=f"growth-chapter-{chapter_no}",
        )
        return write_memory_file(self._memdir_dir, entry, body="\n\n".join(body_parts))


def _parse_structured_output(raw: str) -> dict[str, Any] | None:
    """从 LLM 文本里解析成长结构化 JSON；失败返 None（调用方据此降级、不推进状态）。

    容错顺序：① ```json fenced``` 块；② 整段当 JSON；③ 第一个 { 到最后一个 } 截取。
    解析出非 dict 也算失败。
    """
    if not raw or not raw.strip():
        return None

    m = _FENCE_RE.search(raw)
    if m is not None:
        obj = _try_json(m.group(1))
        if obj is not None:
            return obj

    obj = _try_json(raw.strip())
    if obj is not None:
        return obj

    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        obj = _try_json(raw[start : end + 1])
        if obj is not None:
            return obj
    return None


def _coerce_chapter_payload(parsed: dict[str, Any]) -> dict[str, Any]:
    """#3 schema 校验/兜底：解析出的 dict 仍可能字段缺失/类型错；规整成可安全落盘的形状。

    - narrative：**强制非空字符串**，否则本章没有真内容可落盘 → raise GrowthChapterError（硬失败）。
    - learned / skill_intents：非 list → []（不让畸形类型流到传记/状态）。
    - persona：非 dict → {}（filter_persona_sections 据此安全跳过演化）。
    - personality：非 str → ""。
    - age_range / report：保持原值，由调用方现有的回落逻辑兜底（age_range 回落状态算值、
      report 回落 narrative），这里不动以免重复逻辑。
    返回的是浅拷贝（不就地改 parsed，便于单测对比与排查）。
    """
    narrative = parsed.get("narrative")
    if not isinstance(narrative, str) or not narrative.strip():
        raise GrowthChapterError("结构化输出缺少非空 narrative")

    out: dict[str, Any] = dict(parsed)
    out["narrative"] = narrative

    learned = parsed.get("learned")
    out["learned"] = learned if isinstance(learned, list) else []

    skill_intents = parsed.get("skill_intents")
    out["skill_intents"] = skill_intents if isinstance(skill_intents, list) else []

    persona = parsed.get("persona")
    out["persona"] = persona if isinstance(persona, dict) else {}

    personality = parsed.get("personality")
    out["personality"] = personality if isinstance(personality, str) else ""

    return out


def _coerce_report(raw: object, *, fallback: str) -> str:
    """把结构化输出的 report 字段规整成非空字符串；缺失 / 空 / 非串则回落 fallback。

    单独成函数便于 mypy 收窄 + 单测：成长 SKILL.md 让 LLM 给一段面向人看的当天汇报，
    但旧协议 / 降级输出可能没有，这时用本章 narrative 兜底，保证 dashboard 汇报栏不空白。
    """
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return fallback.strip()


def _try_json(text: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None
