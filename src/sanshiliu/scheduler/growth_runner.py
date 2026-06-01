"""成长执行器；闸门通过后被调用，让 engine 跑一章"成长梦"并落地传记 + 推进状态。

设计要点（平移自 dream_runner.py，但产物不同）：
- **累积传记前置注入**：把前几章的传记摘要拼进 user_text，让本章逻辑自洽地承接前文，
  而不是靠 LLM 自己调 LoadMemory（合成 session 的 channel=growth 没有历史可读）。
- **合成 session**：每章新建 Session.new(channel="growth", user_id="growth")，不污染真实
  用户会话历史；成长本身也会写一条 sessions 表记录，dashboard 可追溯。
- **结构化输出 + 优雅降级**：成长协议强制 LLM 输出单个 JSON `{narrative, age_range,
  learned, personality, skill_intents}`。解析失败（无 JSON / 畸形）→ 记日志、**不推进状态**、
  直接 return；绝不能让脏数据污染状态机或写半截传记。
- **确定性落盘**：传记由代码用 write_memory_file 写 `reference_growth-chapter-N.md`
  （name 满足 SaveMemory 的合规字符集，仅字母数字与连字符下划线），不依赖 LLM 自己调
  SaveMemory（合成 session 里工具是否可靠不保证；落盘必须确定）。
- **错误不冒泡**：所有失败都吞掉记日志；后台任务不能因为一次成长失败而退出。

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

# 从 LLM 输出里抠 JSON：优先 ```json fenced``` 块，其次裸 {...}。成长协议要求只输出一个 JSON。
_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


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

    async def __call__(self, _ctx: dict[str, Any]) -> None:
        """OnDueCallback 签名；闸门已在 scheduler 侧判过，这里再读一次状态防并发竞态。"""
        try:
            await self._run_one_chapter()
        except Exception as exc:
            # 后台任务最外层兜底：任何意外都不能让 scheduler tick 崩
            _logger.error("成长执行器未预期异常（已吞）", error=str(exc))

    async def _run_one_chapter(self) -> None:
        state = load_growth_state(
            self._state_path,
            start_age=self._start_age,
            years_per_chapter=self._years_per_chapter,
            end_age=self._end_age,
        )
        if not state.can_advance():
            _logger.info(
                "成长已定格，跳过", current_chapter=state.current_chapter, end=state.end_chapter
            )
            return

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
        token = enter_growth_autoallow()
        try:
            result = await self._engine.complete_turn(session, prompt)
        except Exception as exc:
            _logger.error(
                "engine.complete_turn 失败（不推进状态）",
                error=str(exc),
                growth_session=session.session_id,
            )
            return
        finally:
            exit_growth_autoallow(token)

        raw_text = result.content if isinstance(result.content, str) else ""
        parsed = _parse_structured_output(raw_text)
        if parsed is None:
            # 优雅降级：畸形 / 无 JSON → 记日志、不写传记、**不推进状态**
            _logger.warning(
                "成长输出未含合法 JSON，降级跳过（不推进状态）",
                growth_session=session.session_id,
                chapter=next_chapter_no,
                raw_preview=raw_text[:200],
            )
            return

        narrative = parsed.get("narrative", "")
        # 优先用 LLM 回的 age_range，缺失/非串则用状态算出的（确保传记标题不空）
        out_age_range = parsed.get("age_range")
        if not isinstance(out_age_range, str) or not out_age_range.strip():
            out_age_range = age_range

        # 确定性落盘：传记写 reference_growth-chapter-N.md（name 合规正则 [A-Za-z0-9_\-]{5,40}）
        try:
            self._write_biography(next_chapter_no, out_age_range, narrative, parsed)
        except Exception as exc:
            _logger.error("成长传记落盘失败（不推进状态）", error=str(exc), chapter=next_chapter_no)
            return

        # PR2 人格整体演化：先把本章演化人格写进版本化目录 data/growth/persona/chapter-N/
        #     （base persona/core 全程不写），再推进 state（advance 把 active_persona_chapter
        #     指到本章 N），最后 invalidate 让 PersonaLoader 下次 get 读到新人格——日常对话即
        #     以"长成的人"回应。任一步失败只记日志、跳过演化，**不影响传记 + 状态推进**。
        self._evolve_persona(next_chapter_no, state.active_persona_chapter, parsed)

        # PR3 技能习得记账：装好的 skill 落成 skills/<id>/，complete_turn 里已由 LLM 经
        # Skill(skill-finder)/installer 有机安装。这里 invalidate+reload 后 diff 目录，
        # 新增 id = 本章真正装上的（目录是真相源，不只信 LLM 自报）。带 source 标记便于 dashboard 追溯。
        installed = self._collect_installed_skills(skills_before, next_chapter_no)

        # R8 每日汇报：dashboard 是汇报展示面（主动推送出 scope）。report 是 LLM 给的"面向人看"的
        # 当天成长汇报；缺失 / 非串则回落到 narrative，确保 dashboard 这一章总有汇报可看、不空白。
        report = _coerce_report(parsed.get("report"), fallback=narrative)
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

    def _collect_installed_skills(self, before: set[str], chapter_no: int) -> list[str]:
        """invalidate+reload 后 diff skill 目录，返回本章新增的 skill id（带审计日志）。

        目录是真相源：只有真把 skills/<id>/SKILL.md 装进去、且能被 loader 解析的才算数；
        LLM 自报装了但目录没有的不计入。无 skill_loader → 直接空列表（不报错）。
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
        if new_ids:
            # 审计：本章自动装了哪些真实 skill（落实 #5 的最低防护之一；另有 tool_calls 表留痕）
            _logger.info(
                "成长本章自动安装 skill（免审批 #5）",
                chapter=chapter_no,
                installed=new_ids,
                source=f"growth-chapter-{chapter_no}",
            )
        else:
            _logger.info(
                "成长本章未安装任何 skill（找不到真实 skill 或无意图）", chapter=chapter_no
            )
        return new_ids

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
