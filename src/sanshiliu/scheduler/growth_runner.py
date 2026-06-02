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

方案 A 解耦（根治"装 skill 失败吃光 turn→整章白跑"）：把一章拆成两相，传记先行、必出：

- **phase-1（零工具、必出）**：complete_turn(use_tools=False) 让 LLM 只产传记 JSON——叙事/人格
  是纯生成，无网络、无工具，杜绝工具循环烧 turn。沿用 #3 一次 LLM 修复 + schema 校验；不可恢复
  （触顶/修复后仍畸形/narrative 空/落盘失败）→ raise GrowthChapterError（heartbeat 标 error，#1）。
  成功 → 写传记 + 演化人格 + **推进状态并落盘**——全部发生在任何安装之前。
- **phase-2（best-effort、bounded）**：状态已推进后，在成长自动放行窗口里，把本章
  learned + skill_intents 合并成安装线索（**每章 ≤ _GROWTH_SKILL_INSTALL_CAP**），由代码固定搜索
  Skills.sh / ClawHub 并装进用户级全局 skills 目录（loader 真正会扫的 skills_dir_global）；找不到就跳过。
  然后目录 diff 记账，回填刚推进那一章的 installed_skills 并重存。**phase-2 失败/超时/异常绝不 raise、
  绝不回退已成立的章**——只记日志，heartbeat 仍视本章为成功（"第 N 章完成，装了 K 个 skill"）。
  只有 phase-1 失败才算 error。

无人值守自动放行 + 非交互 npm 环境只圈 **phase-2**（phase-1 无工具）；窗口/环境都在 finally 复位，
绝不外溢到别的请求。目录是真相源：只有真装进 skills/<id>/SKILL.md、能被 loader 解析的才记账。

PR2 已实现：跑一章 → 写传记 → **整体演化人格（版本化覆盖 base core）** → 推进状态。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shlex
import shutil
import sys
import time
from dataclasses import dataclass
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
    from sanshiliu.security.permission import PermissionManager
    from sanshiliu.skills.loader import SkillLoader
    from sanshiliu.storage.db import Database

_logger = get_logger(__name__)

_GROWTH_CHANNEL = "growth"
_GROWTH_USER_ID = "growth"

# phase-1 传记是零工具纯生成（1 轮即出 JSON），但留点余量给"修复重发"以外的偶发——给 4 轮足够。
# 注意 phase-1 传 use_tools=False，根本不会进工具循环，这个上限实际上很难够到。
_GROWTH_PHASE1_MAX_TURNS = 4

# phase-2 best-effort 装 skill 的 tool 预算：发现（skill-finder）+ 装（skill-installer）每个 intent
# 约 2-3 轮，每章 ≤ 3 个 intent，8 轮够用且有上限（失控也烧不久；触顶不影响已成立的章）。
_GROWTH_PHASE2_MAX_TURNS = 8

# 每章 phase-2 最多尝试装几个 skill——按研究：clawhub 有 server 限流、弱模型自选 slug 供应链风险，
# 都要求克制。超过此数的 skill_intent 不带进 phase-2 prompt（防一章狂装）。
_GROWTH_SKILL_INSTALL_CAP = 3

# phase-2 跑 npx/installer 的子进程非交互 + fail-fast npm 环境（杜绝 stdin 阻塞 + 冷拉久挂）。
# 只在 phase-2 窗口内 set 到 os.environ，finally 复位——绝不全局污染（dream/日常对话不受影响）。
_GROWTH_NPM_ENV: dict[str, str] = {
    "CI": "true",
    "npm_config_yes": "true",
    "npm_config_fetch_timeout": "20000",
    "npm_config_fetch_retries": "1",
    "npm_config_audit": "false",
    "npm_config_fund": "false",
    "npm_config_progress": "false",
}

# phase-2 直接发现/安装用的 CLI（与 preflight 预热版本保持一致）。走代码驱动而不是让 LLM
# 自由读文件/循环，可以避免日志里那种一直找错 CHAPTER.md 路径直到 tool turn 触顶。
_GROWTH_SKILLS_NPX_PKG = "skills@1.5.9"
_GROWTH_CLAWHUB_NPX_PKG = "clawhub@0.18.0"

# 小规模别名表：成长输出常是中文领域词，但公开 skill 生态多用英文检索。
# 这里只放高频、低歧义词；未知领域仍按原文搜索。
_GROWTH_QUERY_ALIASES: dict[str, tuple[str, ...]] = {
    "粤语": ("Cantonese language", "Cantonese", "粤语"),
    "广东话": ("Cantonese language", "Cantonese", "广东话"),
    "cantonese": ("Cantonese language", "Cantonese"),
    "潮汕话": ("Teochew language", "Chaoshan dialect", "潮汕话"),
    "普通话": ("Mandarin Chinese", "Mandarin", "普通话"),
    "脱口秀": ("standup comedy", "talk show", "脱口秀"),
    "打油诗": ("Chinese doggerel poetry", "poetry", "打油诗"),
}

# 从 LLM 输出里抠 JSON：优先 ```json fenced``` 块，其次裸 {...}。成长协议要求只输出一个 JSON。
_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_SKILLS_REF_RE = re.compile(r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([A-Za-z0-9_.\-/]+)")
_CLAWHUB_SLUG_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)\s+@?[A-Za-z0-9_.-]*\s+")

# SkillLoader 发现 skill 的固定文件名；安装后就地核对"落点是否被 loader 扫得到"时复用。
_SKILL_MD_FILENAME = "SKILL.md"


@dataclass(frozen=True)
class _CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class _SkillCandidate:
    source: str  # "skills.sh" | "clawhub"
    skill_id: str
    query: str
    repo: str | None = None
    path: str | None = None
    slug: str | None = None


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
        skill_install_timeout_sec: int = 60,
        skills_dir_global: Path | None = None,
        permission_manager: PermissionManager | None = None,
        db: Database | None = None,
    ) -> None:
        self._engine = engine
        self._state_path = growth_state_path
        self._memdir_dir = memdir_dir
        self._start_age = start_age
        self._years_per_chapter = years_per_chapter
        self._end_age = end_age
        # phase-2 装 skill 的 bash 硬超时（秒）——写进 prompt 让 LLM 据此给 bash 的 timeout_sec，
        # 防 npx 冷拉/无 TTY 挂死把 phase-2 拖久；默认 60，serve 由 config 透传。
        self._skill_install_timeout_sec = skill_install_timeout_sec
        # phase-2 安装 prompt 要据实写出 installer 真正的落点——即 loader 扫的那条用户级全局 skills 目录
        # （= settings.skills_dir_global）。get_settings 已把它回写进 os.environ，故 installer 脚本默认即装
        # 到这里，prompt 里报这条绝对路径才不会和实际落点错位（旧 bug #2：prompt 硬写 ./.sanshiliu/skills）。
        # 缺它（单测/不传）→ prompt 退回不点名具体目录，只说"装进系统默认的用户级全局 skills 目录"。
        self._skills_dir_global = skills_dir_global
        # direct phase-2 会自己跑 npx / installer；这里接入同一个 PermissionManager，确保
        # settings.deny / critical-hard-deny 仍是硬边界。defaultMode=ask 时由成长 autoallow 放行。
        self._permission_manager = permission_manager
        # direct phase-2 不经 engine._record_tool_call；接入同一个 Database 让每条外部命令仍落
        # tool_calls 审计表（#3）——否则无人值守自动安装在 ask 模式下 DB 全程无痕。缺它则跳过记账。
        self._db = db
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

        # ── phase-1（零工具、必出）：产传记 JSON → 写传记 + 演化人格 + 推进状态并落盘 ──
        # 全程在任何安装之前完成；不可恢复则 raise（heartbeat 标 error，#1）。
        out_age_range, coerced = await self._run_phase1_biography(
            state, next_chapter_no, age_range
        )

        # ── phase-2（best-effort、bounded）：状态已推进，再装本章 skill_intents（≤cap），失败绝不回退 ──
        # 注意：传给 phase-2 的是"刚推进的那一章"年龄段/意图；它只回填 installed_skills、绝不动章本身。
        skill_intents = _derive_skill_install_intents(coerced)
        installed = await self._run_phase2_install(
            next_chapter_no,
            out_age_range,
            skill_intents,
        )

        skill_tail = f"，装了 {len(installed)} 个 skill" if installed else ""
        return f"第 {state.current_chapter} 章已完成（{out_age_range} 岁，{state.age} 岁）{skill_tail}"

    async def _run_phase1_biography(
        self, state: GrowthState, next_chapter_no: int, age_range: str
    ) -> tuple[str, dict[str, Any]]:
        """phase-1：零工具产传记 JSON → 写传记 + 演化人格 + 推进状态并落盘。返回 (年龄段, coerced)。

        传记是纯生成（叙事/人格无网络、无工具），用 complete_turn(use_tools=False) 杜绝工具循环
        烧 turn——这是"必出"的部分。沿用 #3 一次 LLM 修复 + schema 校验；触顶/修复后仍畸形/
        narrative 空/落盘失败都属不可恢复 → raise GrowthChapterError（由 __call__ 上抛给 heartbeat 标
        error，#1）。成功路径里 installed_skills 先留空 []——真正安装在 phase-2，回填到同一章。
        """
        prompt = self._build_prompt(state, next_chapter_no, age_range)
        session = Session.new(channel=_GROWTH_CHANNEL, user_id=_GROWTH_USER_ID)
        _logger.info(
            "成长 phase-1（传记，零工具）开始",
            growth_session=session.session_id,
            chapter=next_chapter_no,
            age_range=age_range,
            prompt_chars=len(prompt),
        )
        # phase-1 不挂工具（use_tools=False），故无需自动放行窗口；纯一轮生成即出 JSON。
        try:
            result = await self._engine.complete_turn(
                session, prompt, max_turns=_GROWTH_PHASE1_MAX_TURNS, use_tools=False
            )
        except Exception as exc:
            # #1：致命失败必须如实上抛给 heartbeat（标 error），不再静默吞成 ok。
            _logger.error(
                "成长 phase-1 complete_turn 失败（不推进状态，上报 error）",
                error=str(exc),
                growth_session=session.session_id,
            )
            raise GrowthChapterError(f"engine.complete_turn 失败：{exc}") from exc

        raw_text = result.content if isinstance(result.content, str) else ""

        # #4：触顶时 content 是固定文案，没有真内容可修——直接当硬失败上抛，不喂给 JSON 修复。
        if raw_text.strip() == TOOL_TURN_LIMIT_MESSAGE:
            _logger.error(
                "成长 phase-1 tool 调用触顶（不推进状态，上报 error）",
                growth_session=session.session_id,
                chapter=next_chapter_no,
                max_turns=_GROWTH_PHASE1_MAX_TURNS,
            )
            raise GrowthChapterError(
                f"第 {next_chapter_no} 章 tool 调用触顶"
                f"（上限 {_GROWTH_PHASE1_MAX_TURNS} 轮），未产出结果"
            )

        # #3：先走纯提取；失败（畸形/无 JSON、但有真内容）→ 一次 LLM 修复重发 → 再解析。
        parsed = _parse_structured_output(raw_text)
        if parsed is None:
            parsed = await self._repair_structured_output(raw_text, session.session_id)
        if parsed is None:
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
        coerced = _coerce_chapter_payload(parsed)  # narrative 空会 raise GrowthChapterError

        narrative = coerced["narrative"]
        # 优先用 LLM 回的 age_range，缺失/非串则用状态算出的（确保传记标题不空）
        out_age_range = coerced.get("age_range")
        if not isinstance(out_age_range, str) or not out_age_range.strip():
            out_age_range = age_range

        # 确定性落盘：传记写 reference_growth-chapter-N.md（name 合规正则 [A-Za-z0-9_\-]{5,40}）
        try:
            self._write_biography(next_chapter_no, out_age_range, narrative, coerced)
        except Exception as exc:
            # 落盘失败 = 本章产物没真正落地，算降级、上抛（phase-1 还没装任何东西，无需记账）。
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

        # R8 每日汇报：report 是 LLM 给的"面向人看"的当天成长汇报；缺失 / 非串则回落 narrative。
        report = _coerce_report(coerced.get("report"), fallback=narrative)
        record = ChapterRecord(
            age_range=out_age_range,
            summary=narrative,
            report=report,
            installed_skills=[],  # phase-2 装好后回填到这一章；phase-1 先留空
        )
        state.advance(record)
        save_growth_state(self._state_path, state)
        # advance 后 active_persona_chapter 已指向本章 N；让 loader 热生效到新人格
        if self._persona_loader is not None:
            self._persona_loader.invalidate()
        _logger.info(
            "成长 phase-1 完成，状态已推进（安装前已落地，绝不被 phase-2 回退）",
            growth_session=session.session_id,
            chapter=state.current_chapter,
            age=state.age,
            active_persona_chapter=state.active_persona_chapter,
        )
        return out_age_range, coerced

    async def _run_phase2_install(
        self,
        chapter_no: int,
        age_range: str,
        skill_intents: list[Any],
    ) -> list[str]:
        """phase-2：best-effort 装本章 skill_intents（≤cap），目录 diff 回填 installed_skills 并重存。

        **绝不抛、绝不回退已成立的章**：任何失败/超时/异常都只记日志、返回已 diff 到的（可能为空）。
        无 skill_loader（单测/未启用）→ 直接 []（跳过安装尝试，章已成立）。无意图 → 也跳过。

        生产路径（有 skills_dir_global）不再把搜索/安装交给 LLM 自由工具循环，而是由代码固定搜索
        Skills.sh / ClawHub 并装进用户级全局 skills 目录；旧调用点缺少全局目录时才回落到 LLM 工具
        循环。自动放行窗口 + 非交互 npm 环境只圈这一段，finally 复位（绝不外溢/全局污染）。
        装完按"装前/装后目录 diff"记账——目录是真相源，不只信过程输出。
        """
        if self._skill_loader is None:
            _logger.info("成长 phase-2 跳过（无 skill_loader）", chapter=chapter_no)
            return []

        capped = self._cap_skill_intents(skill_intents)
        if not capped:
            _logger.info("成长 phase-2 跳过（本章无 skill_intent）", chapter=chapter_no)
            return []

        # 装前快照——phase-1 零工具没装任何东西，故此刻基线 = 章开始时的目录。
        skills_before = self._snapshot_skill_ids()
        session = Session.new(channel=_GROWTH_CHANNEL, user_id=_GROWTH_USER_ID)
        _logger.info(
            "成长 phase-2（装 skill，best-effort）开始",
            growth_session=session.session_id,
            chapter=chapter_no,
            intents=len(capped),
            skills_before=len(skills_before),
            mode="direct" if self._skills_dir_global is not None else "llm-fallback",
        )

        # 自动放行窗口 + 非交互 npm 环境：只圈 phase-2；直接安装也会先走 PermissionManager.check，
        # 所以 defaultMode=ask 时仍由 GrowthAutoConfirmer 放行，settings.deny/critical 仍能拦住。
        token = enter_growth_autoallow()
        try:
            with self._scoped_npm_env():
                if self._skills_dir_global is not None:
                    await self._install_skill_intents_directly(
                        chapter_no=chapter_no,
                        intents=capped,
                        session_id=session.session_id,
                    )
                else:
                    prompt = self._build_install_prompt(chapter_no, age_range, capped)
                    await self._engine.complete_turn(
                        session, prompt, max_turns=_GROWTH_PHASE2_MAX_TURNS, use_tools=True
                    )
        except Exception as exc:
            # phase-2 失败绝不影响已成立的章——只记日志，照样去 diff 看装上了没（可能装了一半）。
            _logger.warning(
                "成长 phase-2 装 skill 失败（不影响已成立的章，继续记账）",
                error=str(exc),
                growth_session=session.session_id,
                chapter=chapter_no,
            )
        finally:
            exit_growth_autoallow(token)

        # 目录 diff 记账：新增 id = 本章真正装上的（目录是真相源）。回填到刚推进那一章并重存。
        installed = self._collect_installed_skills(skills_before, chapter_no)
        if installed:
            self._backfill_installed_skills(chapter_no, installed)
        return installed

    def _cap_skill_intents(self, skill_intents: list[Any]) -> list[dict[str, Any]]:
        """规整 + 截断 skill_intents：只取形如 {domain, why} 的 dict，最多 _GROWTH_SKILL_INSTALL_CAP 个。

        每章装 skill 上限——按研究，clawhub 有 server 限流、弱模型自选 slug 有供应链风险，都要求克制。
        """
        out: list[dict[str, Any]] = []
        for item in skill_intents:
            if not isinstance(item, dict):
                continue
            out.append(item)
            if len(out) >= _GROWTH_SKILL_INSTALL_CAP:
                break
        return out

    async def _install_skill_intents_directly(
        self,
        *,
        chapter_no: int,
        intents: list[dict[str, Any]],
        session_id: str,
    ) -> None:
        """代码驱动的 phase-2：按 intent 搜索真实 skill，然后安装到全局 skills 目录。

        线上日志里的失败点是 LLM 自由探索时反复读错路径、耗尽 tool turns。这里把机械部分收回
        到代码里：固定跑 `skills find` / `clawhub search+inspect`，固定把 GitHub 或 ClawHub 候选
        装到 loader 会扫的用户级全局目录。找不到候选或命令失败都只跳过该 intent。
        """
        for intent in intents:
            try:
                await self._handle_one_install_intent(
                    intent, chapter_no=chapter_no, session_id=session_id
                )
            except Exception as exc:
                # 单条意图的意外异常不该带走其余意图（外层只有一个粗 except）；记日志后继续下一条。
                _logger.warning(
                    "成长 skill 单条意图处理异常（跳过该条，不影响其余）",
                    chapter=chapter_no,
                    domain=_intent_domain(intent),
                    error=str(exc),
                )

    async def _handle_one_install_intent(
        self,
        intent: dict[str, Any],
        *,
        chapter_no: int,
        session_id: str,
    ) -> None:
        """处理单条安装意图：领域空 / 已被现有 skill 覆盖 / 找不到候选都跳过，找到则安装。"""
        domain = _intent_domain(intent)
        if not domain:
            return
        if self._intent_already_covered(intent):
            _logger.info(
                "成长 skill 意图已被现有 skill 覆盖，跳过",
                chapter=chapter_no,
                domain=domain,
            )
            return

        candidate = await self._discover_skill_candidate(intent, session_id=session_id)
        if candidate is None:
            _logger.info(
                "成长 skill 未找到可安装候选，跳过",
                chapter=chapter_no,
                domain=domain,
            )
            return
        await self._install_skill_candidate(
            candidate,
            chapter_no=chapter_no,
            session_id=session_id,
        )

    async def _discover_skill_candidate(
        self,
        intent: dict[str, Any],
        *,
        session_id: str,
    ) -> _SkillCandidate | None:
        """按一个 intent 搜索候选；优先 ClawHub，再 Skills.sh。

        ClawHub 对中文/语言类查询命中更好（如 `Cantonese language` → `language-learning`），
        Skills.sh 则在 GitHub `owner/repo@skill` 形态上更容易直接安装。两边都只取第一个能
        通过基本相关性校验的候选。
        """
        queries = _queries_for_intent(intent)
        for query in queries:
            candidate = await self._search_clawhub(query, session_id=session_id)
            if candidate is not None:
                return candidate
        for query in queries:
            candidate = await self._search_skills_sh(query, session_id=session_id)
            if candidate is not None:
                return candidate
        return None

    async def _search_skills_sh(
        self,
        query: str,
        *,
        session_id: str,
    ) -> _SkillCandidate | None:
        npx = shutil.which("npx")
        if npx is None:
            _logger.warning("成长 skill 搜索跳过：未找到 npx", source="skills.sh", query=query)
            return None
        result = await self._run_checked_command(
            [npx, "--yes", _GROWTH_SKILLS_NPX_PKG, "find", query],
            session_id=session_id,
            timeout_sec=self._skill_install_timeout_sec,
        )
        text = _strip_ansi(result.stdout + "\n" + result.stderr)
        if result.returncode != 0:
            _logger.warning(
                "成长 skills.sh 搜索失败",
                query=query,
                returncode=result.returncode,
                output=text[:500],
            )
            return None
        for match in _SKILLS_REF_RE.finditer(text):
            repo = match.group(1)
            path = match.group(2)
            line = _line_containing(text, match.group(0))
            if not _candidate_matches_query(line, query):
                continue
            return _SkillCandidate(
                source="skills.sh",
                skill_id=Path(path).name,
                query=query,
                repo=repo,
                path=path,
            )
        return None

    async def _search_clawhub(
        self,
        query: str,
        *,
        session_id: str,
    ) -> _SkillCandidate | None:
        npx = shutil.which("npx")
        if npx is None:
            _logger.warning("成长 skill 搜索跳过：未找到 npx", source="clawhub", query=query)
            return None
        result = await self._run_checked_command(
            [npx, "--yes", _GROWTH_CLAWHUB_NPX_PKG, "search", query, "--limit", "3"],
            session_id=session_id,
            timeout_sec=self._skill_install_timeout_sec,
        )
        text = _strip_ansi(result.stdout + "\n" + result.stderr)
        if result.returncode != 0:
            _logger.warning(
                "成长 ClawHub 搜索失败",
                query=query,
                returncode=result.returncode,
                output=text[:500],
            )
            return None
        slug = _first_clawhub_slug(text, query)
        if slug is None:
            return None

        inspect = await self._run_checked_command(
            [
                npx,
                "--yes",
                _GROWTH_CLAWHUB_NPX_PKG,
                "inspect",
                slug,
                "--files",
                "--json",
            ],
            session_id=session_id,
            timeout_sec=self._skill_install_timeout_sec,
        )
        inspect_text = _strip_ansi(inspect.stdout + "\n" + inspect.stderr)
        if inspect.returncode != 0:
            _logger.warning(
                "成长 ClawHub inspect 失败",
                query=query,
                slug=slug,
                returncode=inspect.returncode,
                output=inspect_text[:500],
            )
            return None
        meta = _parse_json_object(inspect_text)
        if not _clawhub_candidate_is_acceptable(meta, query):
            _logger.info("成长 ClawHub 候选相关性/安全性不足，跳过", query=query, slug=slug)
            return None
        sec_status = _clawhub_security_status(meta)
        if sec_status != "clean":
            # 缺失/unknown 安全信号按"可接受"放行（已接受的供应链风险）；此处审计留痕便于事后追溯。
            _logger.warning(
                "成长 ClawHub 候选无 clean 安全信号但仍接受（已接受的供应链风险，审计留痕）",
                query=query,
                slug=slug,
                security=sec_status or "absent",
            )
        skill_id = _clawhub_skill_id(meta) or slug
        return _SkillCandidate(
            source="clawhub",
            skill_id=skill_id,
            query=query,
            slug=slug,
        )

    async def _install_skill_candidate(
        self,
        candidate: _SkillCandidate,
        *,
        chapter_no: int,
        session_id: str,
    ) -> None:
        """安装一个已发现候选。失败只记日志；是否真装上由后续目录 diff 判定。"""
        dest = self._skills_dir_global
        if dest is None:
            return
        if (dest / candidate.skill_id).is_dir():
            _logger.info(
                "成长 skill 候选已存在，跳过安装",
                chapter=chapter_no,
                skill=candidate.skill_id,
                source=candidate.source,
            )
            return

        if candidate.source == "skills.sh" and candidate.repo and candidate.path:
            script = _skill_installer_script()
            args = [
                sys.executable,
                str(script),
                "--repo",
                candidate.repo,
                "--path",
                candidate.path,
                "--dest",
                str(dest),
            ]
        elif candidate.source == "clawhub" and candidate.slug:
            npx = shutil.which("npx")
            if npx is None:
                _logger.warning("成长 ClawHub 安装跳过：未找到 npx", skill=candidate.skill_id)
                return
            # clawhub 的 --dir 是相对 workdir 的目录；用 parent+basename 精确落到全局 skills 目录。
            args = [
                npx,
                "--yes",
                _GROWTH_CLAWHUB_NPX_PKG,
                "--no-input",
                "--workdir",
                str(dest.parent),
                "--dir",
                dest.name,
                "install",
                candidate.slug,
            ]
        else:
            return

        before = _skill_dir_names(dest)
        result = await self._run_checked_command(
            args,
            session_id=session_id,
            timeout_sec=self._skill_install_timeout_sec,
        )
        output = _strip_ansi(result.stdout + "\n" + result.stderr)
        if result.returncode != 0:
            _logger.warning(
                "成长 skill 安装命令失败",
                chapter=chapter_no,
                skill=candidate.skill_id,
                source=candidate.source,
                query=candidate.query,
                returncode=result.returncode,
                output=output[:800],
            )
            return
        # 命令报成功，但 loader 会扫的位置没冒出新 skill 目录——多半是 clawhub --dir/--workdir 落点
        # 不对（旧 SKILL.md 警告过的"装到系统扫不到的目录"）。把这种静默失败升级成 WARN，否则它
        # 只表现为"installed 永远是 0"、线上极难定位。目录 diff 仍按真相源记账，故不会误报成功。
        if _skill_dir_names(dest) <= before:
            _logger.warning(
                "成长 skill 安装命令报成功但目标目录未出现新 skill（落点可能不对，loader 扫不到）",
                chapter=chapter_no,
                skill=candidate.skill_id,
                source=candidate.source,
                query=candidate.query,
                dest=str(dest),
                output=output[:800],
            )
            return
        _logger.info(
            "成长 skill 安装命令完成",
            chapter=chapter_no,
            skill=candidate.skill_id,
            source=candidate.source,
            query=candidate.query,
        )

    async def _run_checked_command(
        self,
        args: list[str],
        *,
        session_id: str,
        timeout_sec: int,
    ) -> _CommandResult:
        """先走权限状态机、再执行外部命令，并把这条命令落 tool_calls 审计表。

        直接 phase-2 不经 ToolDispatcher，故两件事都得自己做：
        - settings.deny / critical-hard-deny 仍是硬边界；ask 路径在 autoallow 窗口内被放行。
          **权限用的命令把首词归一化成裸程序名**（去目录 + 去 .exe/.cmd），否则 args[0] 是
          shutil.which / sys.executable 的绝对路径，自然写法 Bash(npx:*) / Bash(python:*) 漏匹配(#1)。
        - 自记 tool_calls（best-effort）：直连不会经 engine._record_tool_call，不记则 ask 模式下
          无人值守自动安装 DB 无痕(#3)。记的是真正执行的命令（含绝对路径），权限决策另存。
        """
        real_command = _format_command(args)
        policy_command = (
            _format_command([_program_name(args[0]), *args[1:]]) if args else real_command
        )
        decision_label = "allow"
        if self._permission_manager is not None:
            decision = await self._permission_manager.check(
                tool_name="bash_exec",
                arguments={"command": policy_command},
                session_id=session_id,
            )
            decision_label = decision.kind
            if decision.kind == "deny":
                _logger.warning(
                    "成长 phase-2 命令被权限拒绝",
                    command=policy_command,
                    rule=decision.rule,
                    source=decision.source,
                )
                denied = _CommandResult(
                    args=tuple(args),
                    returncode=126,
                    stdout="",
                    stderr=f"权限拒绝：{decision.reason or decision.rule or decision.source}",
                )
                await self._audit_command(session_id, real_command, denied, decision_label)
                return denied
        start = time.monotonic()
        result = await self._run_command(args, timeout_sec=timeout_sec)
        latency_ms = int((time.monotonic() - start) * 1000)
        await self._audit_command(
            session_id, real_command, result, decision_label, latency_ms=latency_ms
        )
        return result

    async def _audit_command(
        self,
        session_id: str,
        command: str,
        result: _CommandResult,
        decision: str,
        *,
        latency_ms: int = 0,
    ) -> None:
        """把一条 phase-2 外部命令落 tool_calls 表（best-effort，落库失败不阻塞安装）。

        直连路径不经 engine._record_tool_call，无人值守自动安装若不在此自记，ask 模式下 DB 无痕(#3)。
        与 engine 同口径：tool_name=bash_exec、arguments=JSON{command}、result 截断、permission 决策另存。
        """
        if self._db is None:
            return
        try:
            await self._db.insert_tool_call(
                session_id=session_id,
                tool_name="bash_exec",
                arguments=json.dumps({"command": command}, ensure_ascii=False, sort_keys=True),
                result_text=_strip_ansi(result.stdout + result.stderr)[:2048],
                is_error=result.returncode != 0,
                latency_ms=latency_ms,
                permission_decision=decision,
            )
        except Exception as exc:
            _logger.warning("成长 phase-2 命令 tool_calls 落库失败（不阻塞）", error=str(exc))

    async def _run_command(self, args: list[str], *, timeout_sec: int) -> _CommandResult:
        """执行 phase-2 外部命令；单独成方法便于单测 monkeypatch。"""
        child_env = {**os.environ, **_GROWTH_NPM_ENV}
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=child_env,
            )
        except OSError as exc:
            return _CommandResult(tuple(args), 127, "", str(exc))
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_sec,
            )
        except TimeoutError:
            with contextlib.suppress(Exception):
                proc.kill()
                await proc.wait()
            return _CommandResult(
                tuple(args),
                124,
                "",
                f"命令超时（{timeout_sec}s）：{_format_command(args)}",
            )
        return _CommandResult(
            tuple(args),
            int(proc.returncode or 0),
            _decode_process_output(stdout_b),
            _decode_process_output(stderr_b),
        )

    def _intent_already_covered(self, intent: dict[str, Any]) -> bool:
        """粗略判断当前已装 skill 是否已覆盖该领域，避免重复装同类 skill。"""
        if self._skill_loader is None:
            return False
        needles = [_normalize_search_text(q) for q in _queries_for_intent(intent)]
        needles = [n for n in needles if n]
        if not needles:
            return False
        try:
            skills = self._skill_loader.list()
        except Exception:
            return False
        for skill in skills:
            haystack = _normalize_search_text(
                " ".join([skill.id, skill.name, skill.description, *skill.keywords])
            )
            if any(needle in haystack or haystack in needle for needle in needles):
                return True
        return False

    def _backfill_installed_skills(self, chapter_no: int, installed: list[str]) -> None:
        """phase-2 装好后，把 installed_skills 回填到刚推进的那一章并重存 state（绝不动章本身）。

        重新 load → 改最后一章（= 本章）的 installed_skills → save。重新 load 是为了不依赖内存里那个
        state 对象（防并发/被改过）；改的只是 installed_skills 这一个附加字段，传记/年龄/章数全不动。
        """
        try:
            fresh = load_growth_state(
                self._state_path,
                start_age=self._start_age,
                years_per_chapter=self._years_per_chapter,
                end_age=self._end_age,
            )
            if fresh.current_chapter == chapter_no and fresh.chapters:
                fresh.chapters[-1].installed_skills = installed
                save_growth_state(self._state_path, fresh)
        except Exception as exc:
            # 回填失败只是 installed_skills 没记上——章本身已落盘成立，绝不当失败上抛。
            _logger.warning(
                "成长 phase-2 回填 installed_skills 失败（章已成立，仅记账缺失）",
                error=str(exc),
                chapter=chapter_no,
            )

    @contextlib.contextmanager
    def _scoped_npm_env(self) -> Any:
        """在 phase-2 窗口内把非交互 + fail-fast npm 环境 set 到 os.environ，退出时复原。

        **只圈 phase-2**（不全局污染 dream/日常对话）：进入时记下被覆盖键的原值，finally 逐键还原
        （原本不存在的删掉、原本有的复原）。bash_exec 的子进程继承 os.environ，故 set 在这里即可让
        npx/installer 子进程拿到 CI=true / npm_config_yes=true 等，杜绝 stdin 阻塞 + 冷拉久挂。
        """
        saved: dict[str, str | None] = {k: os.environ.get(k) for k in _GROWTH_NPM_ENV}
        os.environ.update(_GROWTH_NPM_ENV)
        try:
            yield
        finally:
            for k, old in saved.items():
                if old is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = old

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

        方案 A：本方法在 phase-2（章已推进后）被调用——故 diff 出的 skill 都是本章 best-effort
        真装上的，记入审计 + 回填本章 installed_skills。reload 同时令后续对话立刻看到新装的 skill。
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

    def _build_install_prompt(
        self, chapter_no: int, age_range: str, intents: list[dict[str, Any]]
    ) -> str:
        """拼 phase-2 安装 prompt：把本章 skill_intents 交给 LLM，让它发现→安装真实 skill 到全局目录。

        传记已在 phase-1 落地，这一段**只做装 skill**：经 Skill(skill-finder) 发现、Skill(skill-installer)
        装进用户级全局 skills 目录（= loader 真正会扫的 skills_dir_global）；找不到就跳过。强调上限/超时/
        非交互由系统保障，别自造 SKILL.md。落点据实写出 self._skills_dir_global——旧 bug #2 这里硬写
        ./.sanshiliu/skills，和实际落点（用户级全局目录）错位，会误导模型乱填 --dest。
        """
        timeout = self._skill_install_timeout_sec
        lines: list[str] = [
            f"你刚完成第 {chapter_no} 章成长（{age_range} 岁），传记已落盘。"
            "现在只做一件事：为这一章学到的本事，去**装上真实存在的 skill**（不写传记、不输出 JSON）。",
            "",
            "本章要找的能力缺口（skill_intent）：",
        ]
        for i, it in enumerate(intents, 1):
            domain = str(it.get("domain") or "").strip() or "（未注明领域）"
            why = str(it.get("why") or "").strip()
            lines.append(f"{i}. 领域：{domain}" + (f"；为什么：{why}" if why else ""))
        lines.append("")
        # 落点据实告知：installer 默认就装到这条用户级全局 skills 目录（get_settings 已把它回写进
        # os.environ，脚本读 SANSHILIU_SKILLS_DIR_GLOBAL 即拿到它），故模型不必手填 --dest；点名具体
        # 路径是 belt-and-suspenders，万一要显式传也传这条，绝不会装到 loader 扫不到的地方。
        if self._skills_dir_global is not None:
            dest_clause = (
                f"**装进用户级全局 skills 目录 `{self._skills_dir_global}`**"
                "（脚本默认就装到这里，不必手填 --dest；若要显式指定，也只用这条路径）。"
            )
        else:
            dest_clause = (
                "**装进系统默认的用户级全局 skills 目录**"
                "（脚本已默认装到那里，不必手填 --dest）。"
            )
        lines.append(
            "做法（每个缺口）：先 `Skill(skill-finder)` 按领域**发现**一个真实存在的 skill"
            "（它会给出 GitHub 形式的 owner/repo + 子目录）；再 `Skill(skill-installer)` 据此"
            + dest_clause
        )
        lines.append(
            f"约束：本章最多装 {_GROWTH_SKILL_INSTALL_CAP} 个；**找不到合适的真实 skill 就跳过这条**"
            "（不要硬凑、不要降低标准）；严禁用 file_write/bash 自造 SKILL.md。"
        )
        lines.append(
            f"每条 bash 命令请设较短超时（不超过 {timeout}s）；非交互环境已配好，不要等待任何确认输入。"
        )
        lines.append(
            "装没装上由系统按 skills 目录 diff 确定性记账，你不必、也不要谎报装了什么；"
            "装完或都跳过后，直接简短说明结果即可，无需输出 JSON。"
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


def _derive_skill_install_intents(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """从本章 payload 派生 phase-2 安装意图：三类来源**轮转交错**，截断到 cap 时各占其一。

    三类来源按优先级：① 显式 skill_intents（LLM 刻意挑的能力缺口，最高信号）；② 从 narrative/
    persona 提取的窄口径语言能力；③ learned 里的具体知识点。**交错而非顺序拼接**——否则任一类
    塞满 cap 都会把别的饿死：learned 在前会饿死 skill_intents（#1），单纯 skill_intents 在前又会
    饿死"只写进 learned 的粤语"（#2，恰是本特性要兜的场景）。轮转后再截断 cap，每类只要非空就
    至少占一个名额。去重按归一化 domain 先到先得：同领域时高优先级那类胜出、并留住自己的 why。
    """
    seen: set[str] = set()

    explicit: list[dict[str, Any]] = []
    raw_intents = parsed.get("skill_intents")
    if isinstance(raw_intents, list):
        for item in raw_intents:
            if isinstance(item, dict):
                _append_intent(explicit, seen, item)

    implicit: list[dict[str, Any]] = []
    for item in _implicit_language_intents(parsed):
        _append_intent(implicit, seen, item)

    learned_out: list[dict[str, Any]] = []
    learned = parsed.get("learned")
    if isinstance(learned, list):
        for item in learned:
            if not isinstance(item, str):
                continue
            domain = _clean_learned_domain(item)
            _append_intent(
                learned_out,
                seen,
                {"domain": domain, "why": "本章 learned 中出现的习得能力"},
            )

    return _interleave(explicit, implicit, learned_out)


def _interleave(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """轮转交错多个已去重的有序组：[g0[0], g1[0], g2[0], g0[1], ...]，空组自动跳过。"""
    out: list[dict[str, Any]] = []
    i = 0
    while True:
        progressed = False
        for g in groups:
            if i < len(g):
                out.append(g[i])
                progressed = True
        if not progressed:
            return out
        i += 1


def _append_intent(out: list[dict[str, Any]], seen: set[str], item: dict[str, Any]) -> None:
    domain = _intent_domain(item)
    if not domain:
        return
    key = _normalize_search_text(domain)
    if not key or key in seen:
        return
    copied = dict(item)
    copied["domain"] = domain
    out.append(copied)
    seen.add(key)


def _implicit_language_intents(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """从 narrative/persona 里只提取明确"学会/会说/掌握 X 语言"的低歧义场景。"""
    texts: list[str] = []
    for key in ("narrative", "personality", "report"):
        val = parsed.get(key)
        if isinstance(val, str):
            texts.append(val)
    persona = parsed.get("persona")
    if isinstance(persona, dict):
        texts.extend(v for v in persona.values() if isinstance(v, str))
    blob = "\n".join(texts)
    out: list[dict[str, Any]] = []
    for lang in ("粤语", "广东话", "Cantonese", "潮汕话", "普通话"):
        if not _mentions_learned_language(blob, lang):
            continue
        out.append(
            {
                "domain": lang,
                "why": "本章叙事/persona 中出现明确语言能力",
            }
        )
    return out


def _mentions_learned_language(text: str, lang: str) -> bool:
    if not text:
        return False
    escaped = re.escape(lang)
    return bool(
        re.search(rf"(学会|会说|能说|掌握|练会)[^。\n，,；;]{{0,12}}{escaped}", text, re.I)
        or re.search(rf"{escaped}[^。\n，,；;]{{0,12}}(学会|会说|能说|掌握|练会)", text, re.I)
    )


def _clean_learned_domain(text: str) -> str:
    s = re.sub(r"\s+", " ", text).strip(" \t\r\n-：:，,。；;")
    s = re.sub(
        r"^(学会了?|学习了?|掌握了?|练会了?|开始会|会说|能说|懂得|熟悉了?)",
        "",
        s,
    ).strip(" \t\r\n-：:，,。；;")
    return s or text.strip()


def _intent_domain(intent: dict[str, Any]) -> str:
    domain = intent.get("domain")
    if not isinstance(domain, str):
        return ""
    return domain.strip()


def _queries_for_intent(intent: dict[str, Any]) -> list[str]:
    domain = _intent_domain(intent)
    if not domain:
        return []
    norm = domain.lower()
    queries: list[str] = []
    for key, aliases in _GROWTH_QUERY_ALIASES.items():
        if key.lower() in norm or key in domain:
            queries.extend(aliases)
            break
    queries.append(domain)
    out: list[str] = []
    seen: set[str] = set()
    for q in queries:
        q = q.strip()
        k = q.lower()
        if q and k not in seen:
            out.append(q)
            seen.add(k)
    return out


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _skill_dir_names(root: Path) -> set[str]:
    """root 下"直接子目录且含 SKILL.md"的名字集合——与 SkillLoader 的发现规则一致。

    安装后就地核对用：命令报成功但这个集合没变大，说明 skill 没落到 loader 会扫的位置。
    读不动目录（不存在/权限）→ 空集（调用方按"没新增"处理，宁可多报一次 WARN）。
    """
    try:
        return {
            p.name
            for p in root.iterdir()
            if p.is_dir() and (p / _SKILL_MD_FILENAME).is_file()
        }
    except OSError:
        return set()


def _normalize_search_text(text: str) -> str:
    return re.sub(r"[\s\-_./@()（）:：,，;；]+", "", text.lower())


def _candidate_matches_query(text: str, query: str) -> bool:
    norm = _normalize_search_text(text)
    if not norm:
        return False
    tokens = [
        t
        for t in re.split(r"[\s\-_./@()（）:：,，;；]+", query.lower())
        if len(t) >= 3 or re.search(r"[\u4e00-\u9fff]", t)
    ]
    if not tokens:
        return True
    return any(_normalize_search_text(t) in norm for t in tokens)


def _line_containing(text: str, needle: str) -> str:
    for line in text.splitlines():
        if needle in line:
            return line
    return text


def _first_clawhub_slug(text: str, query: str) -> str | None:
    for line in text.splitlines():
        clean = line.strip()
        if not clean or clean.startswith("- "):
            continue
        match = _CLAWHUB_SLUG_RE.match(clean)
        if match is None:
            continue
        slug = match.group(1)
        if _candidate_matches_query(clean, query):
            return slug
    return None


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    obj = _try_json(raw.strip())
    if obj is not None:
        return obj
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        return _try_json(raw[start : end + 1])
    return None


def _clawhub_security_status(meta: dict[str, Any] | None) -> str:
    """取 ClawHub inspect 的 security.status（小写）；任一层缺失 → ""。仅供审计日志/相关性判断用。"""
    if not isinstance(meta, dict):
        return ""
    version = meta.get("version")
    if not isinstance(version, dict):
        return ""
    security = version.get("security")
    if not isinstance(security, dict):
        return ""
    return str(security.get("status") or "").lower()


def _clawhub_candidate_is_acceptable(meta: dict[str, Any] | None, query: str) -> bool:
    """ClawHub inspect 元数据的相关性 + 最低安全门（best-effort）。

    相关性：候选文本须与 query 沾边，否则 False。安全：只拒绝**显式**为坏的信号
    （security.status 非 clean/unknown，或 skillspector 推荐非 SAFE/ALLOW/PASS）。
    **缺失 / unknown 的安全信息按"可接受"放行**——这是 CLAUDE.md 记录的、已接受的供应链风险
    （成长无人值守自动装 skill）；真正的硬边界是 settings.deny / PathGuard / critical-hard-deny。
    接受但无 clean 信号时由 `_search_clawhub` 审计留痕。
    """
    if not isinstance(meta, dict):
        return False
    skill = meta.get("skill")
    version = meta.get("version")
    latest = meta.get("latestVersion")
    text_parts: list[str] = []
    if isinstance(skill, dict):
        for key in ("slug", "displayName", "summary"):
            val = skill.get(key)
            if isinstance(val, str):
                text_parts.append(val)
        tags = skill.get("tags")
        if isinstance(tags, dict):
            text_parts.extend(str(k) for k in tags)
    if isinstance(latest, dict):
        changelog = latest.get("changelog")
        if isinstance(changelog, str):
            text_parts.append(changelog)
    if not _candidate_matches_query(" ".join(text_parts), query):
        return False
    if isinstance(version, dict):
        security = version.get("security")
        if isinstance(security, dict):
            status = str(security.get("status") or "").lower()
            if status and status not in {"clean", "unknown"}:
                return False
            scanners = security.get("scanners")
            if isinstance(scanners, dict):
                skillspector = scanners.get("skillspector")
                if isinstance(skillspector, dict):
                    recommendation = str(skillspector.get("recommendation") or "").upper()
                    if recommendation and recommendation not in {"SAFE", "ALLOW", "PASS"}:
                        return False
    return True


def _clawhub_skill_id(meta: dict[str, Any] | None) -> str | None:
    if not isinstance(meta, dict):
        return None
    skill = meta.get("skill")
    if not isinstance(skill, dict):
        return None
    slug = skill.get("slug")
    return slug if isinstance(slug, str) and slug.strip() else None


def _skill_installer_script() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "skills"
        / "skill-installer"
        / "scripts"
        / "install-skill-from-github.py"
    )


def _format_command(args: list[str]) -> str:
    try:
        return shlex.join(args)
    except Exception:
        return " ".join(args)


def _program_name(path: str) -> str:
    """可执行路径 → 裸程序名（去目录 + 去 .exe/.cmd/.bat/.com 扩展）。

    让 settings.deny/allow 的 verb 写法 Bash(npx:*) / Bash(python:*) 能匹配直连命令——否则首词
    是 shutil.which / sys.executable 的绝对路径，verb 精确比对会漏过(#1)。注意版本化解释器
    （如 python3.13）仍非裸 "python"，那种需用 glob 规则 Bash(*python*) 兜。
    """
    name = Path(path).name
    lower = name.lower()
    for ext in (".exe", ".cmd", ".bat", ".com"):
        if lower.endswith(ext):
            return name[: -len(ext)]
    return name


def _decode_process_output(b: bytes | None) -> str:
    if not b:
        return ""
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return b.decode("mbcs", errors="replace")
        except LookupError:
            return b.decode("utf-8", errors="replace")


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
