"""卡锻造 phase-2：按本章安装意图自动发现并安装**真实** skill（best-effort、bounded）。

平移自 scheduler/growth_runner 的直连 phase-2 机制（老链路冻结待退役，这里是新链路唯一一份），
行为边界原样保留：

- **绝不抛、绝不影响已成立的章**：任何失败/超时/异常只记日志；装没装上按"装前/装后
  skills 目录 diff"确定性记账（目录是真相源，不信过程输出）。
- **机械部分代码驱动**：固定跑 `npx skills find` / `npx clawhub search+inspect`，固定装到
  loader 真正会扫的用户级全局 skills 目录——不交给 LLM 自由工具循环（老链路教训：模型
  反复读错路径耗尽 tool turns）。老链路的"无全局目录时回落 LLM 工具循环"分支**不再保留**：
  缺 skills_dir_global 时 ForgeRunner 干脆不构造本安装器、phase-2 整体跳过（语义更简单）。
- **权限/审计不绕过**：每条外部命令先过 PermissionManager.check（settings.deny / PathGuard /
  critical-hard-deny 仍是硬边界；ask 路径由锻造自动放行窗口放行——复用
  security/growth_approvals 的 contextvar 窗口，只圈本 phase、finally 复位），并自记
  tool_calls 审计表。多卡放大供应链风险是主人明知并接受的决策（见 抽卡平台-设计方案.md
  决策 #6）；护栏是每章 ≤ PER_CHAPTER_INSTALL_CAP + 每卡总上限（ForgeRunner 扣预算传入）。
- **非交互 npm 环境只圈本窗口**：CI=true / npm_config_yes 等 set 进 os.environ 后 finally
  逐键复原，绝不外溢到 dream/日常对话。
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

from sanshiliu.foundation.frontmatter import parse as _parse_frontmatter
from sanshiliu.foundation.logging import get_logger
from sanshiliu.gacha.structured import parse_json_object
from sanshiliu.security.growth_approvals import (
    enter_growth_autoallow,
    exit_growth_autoallow,
)

if TYPE_CHECKING:
    from sanshiliu.security.permission import PermissionManager
    from sanshiliu.skills.loader import SkillLoader
    from sanshiliu.storage.db import Database

_logger = get_logger(__name__)

# 每章最多装几个 skill——clawhub 有 server 限流、弱模型自选 slug 有供应链风险，都要求克制。
# 每卡总上限由 ForgeRunner 按 config gacha_skills_per_card_cap 扣预算后经 max_installs 传入。
PER_CHAPTER_INSTALL_CAP = 3

# 跑 npx/installer 的子进程非交互 + fail-fast npm 环境（杜绝 stdin 阻塞 + 冷拉久挂）。
_NPM_ENV: dict[str, str] = {
    "CI": "true",
    "npm_config_yes": "true",
    "npm_config_fetch_timeout": "20000",
    "npm_config_fetch_retries": "1",
    "npm_config_audit": "false",
    "npm_config_fund": "false",
    "npm_config_progress": "false",
}

# 直接发现/安装用的 CLI（与 serve preflight 预热版本保持一致；与老链路同版本）。
_SKILLS_NPX_PKG = "skills@1.5.9"
_CLAWHUB_NPX_PKG = "clawhub@0.18.0"

# 小规模别名表：锻造输出常是中文领域词，但公开 skill 生态多用英文检索。
# 只放高频、低歧义词；未知领域仍按原文搜索。
_QUERY_ALIASES: dict[str, tuple[str, ...]] = {
    "storytelling_timing": ("storytelling", "public speaking", "standup comedy"),
    "公共演讲": ("public speaking", "presentation", "storytelling"),
    "喜剧表演": ("standup comedy", "improv comedy", "acting"),
    "喜剧写作": ("comedy writing", "standup comedy", "humor writing"),
    "创意写作": ("creative writing", "writing", "content creation"),
    "表演和台词": ("acting", "script writing", "public speaking"),
    "音频制作": ("audio production", "podcasting", "voice acting"),
    "讲故事": ("storytelling", "public speaking", "standup comedy"),
    "讲古": ("storytelling", "oral storytelling", "public speaking"),
    "叙事": ("storytelling", "creative writing", "public speaking"),
    "口语节奏": ("public speaking", "storytelling", "standup comedy"),
    "节奏感": ("public speaking", "storytelling", "standup comedy"),
    "节奏控制": ("public speaking", "storytelling", "standup comedy"),
    "笑点": ("standup comedy", "comedy writing", "humor writing"),
    "包袱": ("standup comedy", "comedy writing", "humor writing"),
    "幽默": ("standup comedy", "comedy writing", "humor writing"),
    "段子": ("standup comedy", "comedy writing", "humor writing"),
    "即兴": ("improv comedy", "public speaking", "standup comedy"),
    "控场": ("public speaking", "presentation", "improv comedy"),
    "表演": ("acting", "public speaking", "storytelling"),
    "台词": ("acting", "script writing", "public speaking"),
    "对白": ("acting", "script writing", "storytelling"),
    "配音": ("voice acting", "dubbing", "acting"),
    "素材": ("creative writing", "content creation", "writing"),
    "写下来再改": ("creative writing", "writing", "content creation"),
    "写作结构": ("creative writing", "comedy writing", "storytelling"),
    "四拍结构": ("comedy writing", "standup comedy", "storytelling"),
    "录音": ("audio production", "podcasting", "voice acting"),
    "电台": ("podcasting", "audio production", "broadcasting"),
    "粤语": ("Cantonese language", "Cantonese", "粤语"),
    "广东话": ("Cantonese language", "Cantonese", "广东话"),
    "cantonese": ("Cantonese language", "Cantonese"),
    "潮汕话": ("Teochew language", "Chaoshan dialect", "潮汕话"),
    "普通话": ("Mandarin Chinese", "Mandarin", "普通话"),
    "脱口秀": ("standup comedy", "talk show", "脱口秀"),
    "打油诗": ("Chinese doggerel poetry", "poetry", "打油诗"),
    # 卡池幻想类世界的高频领域：公开库没有"修仙技能"，映射到最接近的真实大类
    "修仙": ("meditation", "taoism", "chinese mythology"),
    "炼丹": ("herbal medicine", "chemistry", "alchemy"),
    "武功": ("martial arts", "kung fu", "fitness"),
    "武术": ("martial arts", "kung fu", "fitness"),
    "剑术": ("fencing", "martial arts", "swordsmanship"),
    "占卜": ("tarot", "astrology", "divination"),
    "风水": ("feng shui", "interior design", "风水"),
    "编程": ("programming", "software development", "coding"),
    "黑客": ("cybersecurity", "ethical hacking", "programming"),
    "投资": ("investing", "personal finance", "stock market"),
    "理财": ("personal finance", "investing", "budgeting"),
    "生存": ("survival skills", "outdoor skills", "first aid"),
    "急救": ("first aid", "emergency response", "medical"),
    "侦探": ("detective", "critical thinking", "investigation"),
    "推理": ("critical thinking", "logic", "detective"),
}

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


class SkillAutoInstaller:
    """按安装意图直连搜索/安装真实 skill；目录 diff 记账，永不抛。

    skill_loader 与 skills_dir_global 是硬依赖（缺任一就别构造本类，phase-2 整体跳过）；
    permission_manager / db 可选（缺则不做权限检查 / 不落审计，与老链路同语义——
    生产装配两者都该传）。
    """

    def __init__(
        self,
        *,
        skill_loader: SkillLoader,
        skills_dir_global: Path,
        permission_manager: PermissionManager | None = None,
        db: Database | None = None,
        timeout_sec: int = 60,
    ) -> None:
        self._skill_loader = skill_loader
        self._skills_dir_global = skills_dir_global
        self._permission_manager = permission_manager
        self._db = db
        self._timeout_sec = timeout_sec

    async def install_for_chapter(
        self,
        *,
        chapter_no: int,
        intents: list[Any],
        session_id: str,
        max_installs: int,
    ) -> list[str]:
        """best-effort 装本章 intents（≤ min(每章上限, max_installs)），返回目录 diff 出的新 id。

        任何失败/超时/异常都只记日志、返回已 diff 到的（可能为空）。max_installs ≤ 0 或
        无有效意图 → 直接 []（跳过安装尝试）。
        """
        capped = self._cap_intents(intents, max_installs)
        if not capped:
            _logger.info("锻造 phase-2 跳过（无安装预算或无有效意图）", chapter=chapter_no)
            return []

        # 装前快照——phase-1 零工具没装任何东西，故此刻基线 = 章开始时的目录。
        skills_before = self._snapshot_skill_ids()
        _logger.info(
            "锻造 phase-2（装 skill，best-effort）开始",
            forge_session=session_id,
            chapter=chapter_no,
            intents=len(capped),
            skills_before=len(skills_before),
        )

        # 自动放行窗口 + 非交互 npm 环境：只圈本段；直连安装仍先走 PermissionManager.check，
        # 所以 defaultMode=ask 时由自动放行 confirmer 放行，settings.deny/critical 仍能拦住。
        # （窗口复用 security/growth_approvals——它是通用的"无人值守安装放行"机制，不只属于老成长。）
        # 非交互 npm 环境不动 os.environ：_run_command 已对每个子进程显式合并 _NPM_ENV——
        # 全局 set 是老链路"LLM 工具循环 fallback"（本模块已砍掉）的遗留，留着只会在
        # 锻造的几分钟窗口里把 CI=true 漏给并发日常对话的 bash_exec 子进程。
        token = enter_growth_autoallow()
        try:
            await self._install_intents_directly(
                chapter_no=chapter_no, intents=capped, session_id=session_id
            )
        except Exception as exc:
            # phase-2 失败绝不影响已成立的章——只记日志，照样去 diff 看装上了没（可能装了一半）。
            _logger.warning(
                "锻造 phase-2 装 skill 失败（不影响已成立的章，继续记账）",
                error=str(exc),
                forge_session=session_id,
                chapter=chapter_no,
            )
        finally:
            exit_growth_autoallow(token)

        return self._collect_installed(skills_before, chapter_no)

    def _cap_intents(self, skill_intents: list[Any], max_installs: int) -> list[dict[str, Any]]:
        """规整 + 截断：只取 dict 形态的意图，最多 min(每章上限, 每卡剩余预算) 个。"""
        cap = min(PER_CHAPTER_INSTALL_CAP, max_installs)
        if cap <= 0:
            return []
        out: list[dict[str, Any]] = []
        for item in skill_intents:
            if not isinstance(item, dict):
                continue
            out.append(item)
            if len(out) >= cap:
                break
        return out

    async def _install_intents_directly(
        self,
        *,
        chapter_no: int,
        intents: list[dict[str, Any]],
        session_id: str,
    ) -> None:
        """逐条意图：发现真实候选 → 安装到全局 skills 目录；单条失败不带走其余意图。"""
        for intent in intents:
            try:
                await self._handle_one_intent(intent, chapter_no=chapter_no, session_id=session_id)
            except Exception as exc:
                _logger.warning(
                    "锻造 skill 单条意图处理异常（跳过该条，不影响其余）",
                    chapter=chapter_no,
                    domain=_intent_domain(intent),
                    error=str(exc),
                )

    async def _handle_one_intent(
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
                "锻造 skill 意图已被现有 skill 覆盖，跳过", chapter=chapter_no, domain=domain
            )
            return

        candidate = await self._discover_candidate(intent, session_id=session_id)
        if candidate is None:
            _logger.info("锻造 skill 未找到可安装候选，跳过", chapter=chapter_no, domain=domain)
            return
        await self._install_candidate(candidate, chapter_no=chapter_no, session_id=session_id)

    def _intent_already_covered(self, intent: dict[str, Any]) -> bool:
        """粗略判断当前已装 skill 是否已覆盖该领域，避免重复装同类（跨卡天然去重：目录全局）。"""
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

    async def _discover_candidate(
        self,
        intent: dict[str, Any],
        *,
        session_id: str,
    ) -> _SkillCandidate | None:
        """按一个 intent 搜索候选；优先 ClawHub（中文/语言类命中更好），再 Skills.sh。"""
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
            _logger.warning("锻造 skill 搜索跳过：未找到 npx", source="skills.sh", query=query)
            return None
        result = await self._run_checked_command(
            [npx, "--yes", _SKILLS_NPX_PKG, "find", query],
            session_id=session_id,
            timeout_sec=self._timeout_sec,
        )
        text = _strip_ansi(result.stdout + "\n" + result.stderr)
        if result.returncode != 0:
            _logger.warning(
                "锻造 skills.sh 搜索失败",
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
            _logger.warning("锻造 skill 搜索跳过：未找到 npx", source="clawhub", query=query)
            return None
        result = await self._run_checked_command(
            [npx, "--yes", _CLAWHUB_NPX_PKG, "search", query, "--limit", "3"],
            session_id=session_id,
            timeout_sec=self._timeout_sec,
        )
        text = _strip_ansi(result.stdout + "\n" + result.stderr)
        if result.returncode != 0:
            _logger.warning(
                "锻造 ClawHub 搜索失败",
                query=query,
                returncode=result.returncode,
                output=text[:500],
            )
            return None
        slug = _first_clawhub_slug(text, query)
        if slug is None:
            return None

        inspect = await self._run_checked_command(
            [npx, "--yes", _CLAWHUB_NPX_PKG, "inspect", slug, "--files", "--json"],
            session_id=session_id,
            timeout_sec=self._timeout_sec,
        )
        inspect_text = _strip_ansi(inspect.stdout + "\n" + inspect.stderr)
        if inspect.returncode != 0:
            _logger.warning(
                "锻造 ClawHub inspect 失败",
                query=query,
                slug=slug,
                returncode=inspect.returncode,
                output=inspect_text[:500],
            )
            return None
        meta = parse_json_object(inspect_text)
        if not _clawhub_candidate_is_acceptable(meta, query):
            _logger.info("锻造 ClawHub 候选相关性/安全性不足，跳过", query=query, slug=slug)
            return None
        sec_status = _clawhub_security_status(meta)
        if sec_status != "clean":
            # 缺失/unknown 安全信号按"可接受"放行（主人明知并接受的供应链风险）；审计留痕便于追溯。
            _logger.warning(
                "锻造 ClawHub 候选无 clean 安全信号但仍接受（已接受的供应链风险，审计留痕）",
                query=query,
                slug=slug,
                security=sec_status or "absent",
            )
        skill_id = _clawhub_skill_id(meta) or slug
        return _SkillCandidate(source="clawhub", skill_id=skill_id, query=query, slug=slug)

    async def _install_candidate(
        self,
        candidate: _SkillCandidate,
        *,
        chapter_no: int,
        session_id: str,
    ) -> None:
        """安装一个已发现候选。失败只记日志；是否真装上由后续目录 diff 判定。"""
        dest = self._skills_dir_global
        if (dest / candidate.skill_id).is_dir():
            _logger.info(
                "锻造 skill 候选已存在，跳过安装",
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
                _logger.warning("锻造 ClawHub 安装跳过：未找到 npx", skill=candidate.skill_id)
                return
            # clawhub 的 --dir 是相对 workdir 的目录；用 parent+basename 精确落到全局 skills 目录。
            args = [
                npx,
                "--yes",
                _CLAWHUB_NPX_PKG,
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
            args, session_id=session_id, timeout_sec=self._timeout_sec
        )
        output = _strip_ansi(result.stdout + "\n" + result.stderr)
        if result.returncode != 0:
            _logger.warning(
                "锻造 skill 安装命令失败",
                chapter=chapter_no,
                skill=candidate.skill_id,
                source=candidate.source,
                query=candidate.query,
                returncode=result.returncode,
                output=output[:800],
            )
            return
        # 命令报成功但 loader 会扫的位置没冒出新目录——多半是落点不对；升级成 WARN（极难定位的静默失败）。
        after_names = _skill_dir_names(dest)
        if after_names <= before:
            _logger.warning(
                "锻造 skill 安装命令报成功但目标目录未出现新 skill（落点可能不对，loader 扫不到）",
                chapter=chapter_no,
                skill=candidate.skill_id,
                source=candidate.source,
                query=candidate.query,
                dest=str(dest),
                output=output[:800],
            )
            return
        _logger.info(
            "锻造 skill 安装命令完成",
            chapter=chapter_no,
            skill=candidate.skill_id,
            source=candidate.source,
            query=candidate.query,
        )
        # 装后修 frontmatter：外部 SKILL.md 常把含冒号的值不加引号写进 frontmatter，YAML 崩、
        # loader 丢弃——装了也用不了。对本次新落地的目录各修一次（best-effort）。
        for name in sorted(after_names - before):
            self._sanitize_installed_skill(dest / name, chapter_no=chapter_no)

    def _sanitize_installed_skill(self, skill_dir: Path, *, chapter_no: int) -> None:
        """装后尽力修一次该 skill 的 SKILL.md frontmatter；修不动/异常只记日志，绝不抛。"""
        skill_md = skill_dir / _SKILL_MD_FILENAME
        if not skill_md.is_file():
            return
        try:
            ok = _sanitize_skill_frontmatter(skill_md)
        except Exception as exc:
            _logger.warning(
                "锻造 skill 装后 frontmatter 修复异常（跳过，不影响已落地）",
                chapter=chapter_no,
                skill=skill_dir.name,
                error=str(exc),
            )
            return
        if ok:
            _logger.info(
                "锻造 skill 装后 frontmatter 就绪（loader 可加载）",
                chapter=chapter_no,
                skill=skill_dir.name,
            )
        else:
            _logger.warning(
                "锻造 skill 装后 frontmatter 仍不可解析（loader 会丢弃，已尽力修）",
                chapter=chapter_no,
                skill=skill_dir.name,
                path=str(skill_md),
            )

    async def _run_checked_command(
        self,
        args: list[str],
        *,
        session_id: str,
        timeout_sec: int,
    ) -> _CommandResult:
        """先走权限状态机、再执行外部命令，并把这条命令落 tool_calls 审计表。

        直连不经 ToolDispatcher，两件事都得自己做：
        - settings.deny / critical-hard-deny 仍是硬边界；ask 路径在自动放行窗口内被放行。
          权限用的命令把首词归一化成裸程序名（去目录 + 去 .exe/.cmd），否则 args[0] 是
          shutil.which / sys.executable 的绝对路径，自然写法 Bash(npx:*) / Bash(python:*) 漏匹配。
        - 自记 tool_calls（best-effort）：不记则 ask 模式下无人值守自动安装 DB 无痕。
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
                    "锻造 phase-2 命令被权限拒绝",
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
        """把一条 phase-2 外部命令落 tool_calls 表（best-effort，落库失败不阻塞安装）。"""
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
            _logger.warning("锻造 phase-2 命令 tool_calls 落库失败（不阻塞）", error=str(exc))

    async def _run_command(self, args: list[str], *, timeout_sec: int) -> _CommandResult:
        """执行 phase-2 外部命令；单独成方法便于单测 monkeypatch。"""
        child_env = {**os.environ, **_NPM_ENV}
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
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
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

    def _snapshot_skill_ids(self) -> set[str]:
        """装前快照已落地 skill 的目录名集合（discover_ids 是 parse-free 目录口径——真相源）。"""
        try:
            return self._skill_loader.discover_ids()
        except Exception as exc:
            _logger.warning("锻造 skill 装前快照失败（记账降级为空）", error=str(exc))
            return set()

    def _collect_installed(self, before: set[str], chapter_no: int) -> list[str]:
        """invalidate 后按目录 diff，返回本章新增的 skill id（带审计日志）。

        invalidate 让 frontmatter 合法的新 skill 下次 list() 被重新解析、对后续对话生效；
        记账用 discover_ids()（目录口径，parse-free），与装前快照同口径。
        """
        try:
            self._skill_loader.invalidate()
            after = self._skill_loader.discover_ids()
        except Exception as exc:
            _logger.warning(
                "锻造 skill 装后目录扫描失败（记账降级为空）", error=str(exc), chapter=chapter_no
            )
            return []
        new_ids = sorted(after - before)
        if new_ids:
            _logger.info(
                "锻造本章自动安装 skill（免审批，已接受风险，审计留痕）",
                chapter=chapter_no,
                installed=new_ids,
                source=f"gacha-chapter-{chapter_no}",
            )
        else:
            _logger.info(
                "锻造本章未安装任何 skill（找不到真实 skill 或无意图）", chapter=chapter_no
            )
        return new_ids


# ────────── 安装意图派生（phase-1 结构化输出 → phase-2 输入） ──────────


def derive_skill_install_intents(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """从本章 payload 派生安装意图：三类来源**轮转交错**，截断到 cap 时各占其一。

    三类来源按优先级：① 显式 skill_intents（LLM 刻意挑的能力缺口，最高信号）；② 从
    narrative/persona 提取的窄口径语言能力；③ learned 里的具体知识点。交错而非顺序拼接——
    否则任一类塞满 cap 都会把别的饿死。去重按归一化 domain 先到先得：同领域时高优先级
    那类胜出、并留住自己的 why。
    """
    seen: set[str] = set()

    explicit: list[dict[str, Any]] = []
    for item in coerce_skill_intents(parsed.get("skill_intents")):
        _append_intent(explicit, seen, item)

    implicit: list[dict[str, Any]] = []
    for item in _implicit_language_intents(parsed):
        _append_intent(implicit, seen, item)

    learned_out: list[dict[str, Any]] = []
    for learned_item in coerce_learned_items(parsed.get("learned")):
        domain = _clean_learned_domain(learned_item)
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
    why = _intent_why(copied)
    if why and not isinstance(copied.get("why"), str):
        copied["why"] = why
    out.append(copied)
    seen.add(key)


def coerce_skill_intents(raw: Any) -> list[dict[str, Any]]:
    """把 LLM 常见的 skill_intents 变体规整成 {domain, why} 列表（兼容层，避免一章白没意图）。

    公开导出：forge_runner 的 schema 校验（_coerce_chapter_payload）也用它规整同一字段。
    """
    if isinstance(raw, dict):
        raw_items: list[Any] = [raw]
    elif isinstance(raw, list):
        raw_items = raw
    elif isinstance(raw, str):
        raw_items = _split_text_items(raw)
    else:
        return []

    out: list[dict[str, Any]] = []
    for item in raw_items:
        if isinstance(item, dict):
            domain = _intent_domain(item)
            if not domain:
                continue
            copied = dict(item)
            copied["domain"] = domain
            why = _intent_why(copied)
            if why:
                copied["why"] = why
            out.append(copied)
        elif isinstance(item, str):
            domain = _clean_intent_domain(item)
            if domain:
                out.append({"domain": domain, "why": "本章 skill_intents 中出现的能力缺口"})
    return out


def coerce_learned_items(raw: Any) -> list[str]:
    """把 learned 规整成字符串列表；只接受能拆成明确条目的字符串/字典/list。

    公开导出：forge_runner 的 schema 校验（_coerce_chapter_payload）也用它规整同一字段。
    """
    if isinstance(raw, list):
        return [item.strip() for item in raw if isinstance(item, str) and item.strip()]
    if isinstance(raw, dict):
        return [item.strip() for item in raw.values() if isinstance(item, str) and item.strip()]
    if not isinstance(raw, str):
        return []
    return _split_text_items(raw, allow_single=_looks_like_learned_item(raw))


def _split_text_items(text: str, *, allow_single: bool = True) -> list[str]:
    """拆 bullet/编号/多行/分号列表，顺手去掉前缀符号。"""
    s = text.strip()
    if not s:
        return []
    has_list_shape = bool(re.search(r"(^|\n)\s*(?:[-*•]|\d+[.、)])\s+", s))
    has_separator = "\n" in s or "；" in s or ";" in s
    if not has_list_shape and not has_separator:
        return [s] if allow_single else []

    parts = re.split(r"\n+|[；;]", s)
    out: list[str] = []
    for part in parts:
        item = re.sub(r"^\s*(?:[-*•]|\d+[.、)])\s*", "", part).strip()
        if item:
            out.append(item)
    return out


def _looks_like_learned_item(text: str) -> bool:
    return bool(
        re.search(
            r"(学会|学习|掌握|练会|开始会|会说|能说|懂得|熟悉|认识到|知道了|接触到|形成|养成)",
            text,
        )
    )


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
        out.append({"domain": lang, "why": "本章叙事/persona 中出现明确语言能力"})
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
    for pattern, domain in (
        (r"粤语|广东话|Cantonese", "粤语"),
        (r"潮汕话|潮州话|Teochew|Chaoshan", "潮汕话"),
        (r"普通话|Mandarin", "普通话"),
        (r"四拍结构|写作结构|写本子|改稿|删.*好笑|作品思维", "喜剧写作"),
        (r"即兴|救场|接住意外", "即兴喜剧"),
        (r"口语节奏|节奏感|节奏控制|节奏|停顿|笑点|包袱", "讲故事节奏"),
        (r"讲古|讲故事|叙事|故事.*结构", "讲故事"),
        (r"站在台上|上台|舞台|观众|控场", "公共演讲"),
        (r"课堂搞笑|搞笑本能|搞笑作品|让人笑", "喜剧表演"),
        (r"配音|模仿.*语调|模仿腔调|台词|对白|表演", "表演和台词"),
        (r"录音|电台|麦克风|播出|演播室|广播电视中心", "音频制作"),
        (r"素材|写下来再改|创作习惯", "创意写作"),
        (r"修炼|炼气|筑基|金丹|元婴|御剑|吐纳", "修仙"),
        (r"炼丹|丹药|药理", "炼丹"),
        (r"拳法|腿法|内功|轻功|招式", "武功"),
        (r"剑法|剑诀|剑意", "剑术"),
        (r"写代码|编程|程序|算法|开发", "编程"),
        (r"炒股|投资|理财|金融|交易", "投资"),
        (r"求生|野外|荒野|生存", "生存"),
        (r"急救|包扎|心肺复苏", "急救"),
        (r"破案|推理|查案|线索", "推理"),
    ):
        if re.search(pattern, s, re.I):
            return domain
    return _trim_search_domain(s or text.strip())


def _clean_intent_domain(text: str) -> str:
    s = re.sub(r"\s+", " ", text).strip(" \t\r\n-：:，,。；;")
    # "对"是裸的高频字（对白/对话/对联…），只在确实是"对…感兴趣"框架时才剥，避免误伤合法领域词
    s = re.sub(
        r"^(希望能学习|希望学习|想学习|学习|对(?=.*感兴趣)|或许需要一些关于|需要一些关于|需要|想找|想要)",
        "",
        s,
    ).strip(" \t\r\n-：:，,。；;")
    s = re.sub(r"(感兴趣|的方法|的方法论|的工具|的技巧)$", "", s).strip(" \t\r\n-：:，,。；;")
    return _trim_search_domain(s or text.strip())


def _trim_search_domain(text: str) -> str:
    """把超长叙事句压到更像搜索词的长度，避免拿整句中文去搜技能市场。"""
    s = text.strip()
    if len(s) <= 32:
        return s
    pieces = re.split(r"[，,。；;：:（(——-]", s)
    for piece in pieces:
        p = piece.strip()
        if 2 <= len(p) <= 32:
            return p
    return s[:32].strip()


def _intent_domain(intent: dict[str, Any]) -> str:
    for key in ("domain", "skill", "intent_name", "name", "topic", "ability"):
        domain = intent.get(key)
        if isinstance(domain, str) and domain.strip():
            return _clean_intent_domain(domain)
    return ""


def _intent_why(intent: dict[str, Any]) -> str:
    for key in ("why", "reason", "rationale", "description"):
        value = intent.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _queries_for_intent(intent: dict[str, Any]) -> list[str]:
    domain = _intent_domain(intent)
    if not domain:
        return []
    norm = domain.lower()
    queries: list[str] = []
    for key, aliases in _QUERY_ALIASES.items():
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


# ────────── 小工具（命令输出 / 目录 / frontmatter / 匹配） ──────────


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _skill_dir_names(root: Path) -> set[str]:
    """root 下"直接子目录且含 SKILL.md"的名字集合——与 SkillLoader 的发现规则一致。"""
    try:
        return {p.name for p in root.iterdir() if p.is_dir() and (p / _SKILL_MD_FILENAME).is_file()}
    except OSError:
        return set()


# frontmatter 里一行顶层标量 `key: value`（key 后须紧跟空白 + 非空值）。带缩进的（嵌套/列表项）
# 不在此列——只修顶层裸标量，避免误伤结构化字段。
_FM_SCALAR_RE = re.compile(r"^([A-Za-z0-9_.-]+):[ \t]+(\S.*)$")


def _sanitize_skill_frontmatter(skill_md: Path) -> bool:
    """装来的 SKILL.md 若 frontmatter YAML 不合法，尽力修一次；返回最终是否可被 loader 解析。

    最小修复：对顶层 `key: value` 里未加引号、且值中含冒号的标量整体补双引号；
    修完能解析且仍含 name/description 才回写，否则保持原文。已能解析 → 不动、返 True。
    """
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return False
    try:
        _parse_frontmatter(text)
        return True
    except ValueError:
        pass

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    end_idx = -1
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = i
            break
    if end_idx < 0:
        return False

    changed = False
    for i in range(1, end_idx):
        new_line = _requote_frontmatter_line(lines[i])
        if new_line != lines[i]:
            lines[i] = new_line
            changed = True
    if not changed:
        return False

    candidate = "\n".join(lines)
    if text.endswith("\n"):
        candidate += "\n"
    try:
        parsed = _parse_frontmatter(candidate)
    except ValueError:
        return False
    if "name" not in parsed.frontmatter or "description" not in parsed.frontmatter:
        return False
    try:
        skill_md.write_text(candidate, encoding="utf-8")
    except OSError:
        return False
    return True


def _requote_frontmatter_line(line: str) -> str:
    """给顶层 `key: value` 中未加引号、含冒号的标量补双引号；其余原样返回。"""
    m = _FM_SCALAR_RE.match(line)
    if m is None:
        return line
    key, value = m.group(1), m.group(2).rstrip()
    if not value or value[0] in "\"'[{>|&*#":
        return line
    if ":" not in value:
        return line
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'{key}: "{escaped}"'


def _normalize_search_text(text: str) -> str:
    return re.sub(r"[\s\-_./@()（）:：,，;；]+", "", text.lower())


def _candidate_matches_query(text: str, query: str) -> bool:
    norm = _normalize_search_text(text)
    if not norm:
        return False
    tokens = [
        t
        for t in re.split(r"[\s\-_./@()（）:：,，;；]+", query.lower())
        if len(t) >= 3 or re.search(r"[一-鿿]", t)
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


def _clawhub_security_status(meta: dict[str, Any] | None) -> str:
    """取 ClawHub inspect 的 security.status（小写）；任一层缺失 → ""。仅供审计/相关性判断。"""
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
    缺失 / unknown 按"可接受"放行——主人明知并接受的供应链风险；真正的硬边界是
    settings.deny / PathGuard / critical-hard-deny。
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
    """可执行路径 → 裸程序名（去目录 + 去 .exe/.cmd/.bat/.com 扩展），让 verb 权限规则能匹配。"""
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
