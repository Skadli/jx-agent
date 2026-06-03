"""dashboard 成长模块读端点（PR4 / prd R9）；GET /api/growth* 读 growth-state.json + 成长产物 → JSON。

设计与 api.py 一致：每个 endpoint 一个 make_<x>_handler 工厂，runner 注入路径（state/data/memdir），
闭包持有；所有处理失败 try/except 回 500、不抛到 dispatcher。

为什么读路径而非读 loader：成长状态/人格/传记都是文件真相源（growth-state.json、
data/growth/persona/chapter-N/、memdir 的 reference_growth-chapter-N_*.md），直接读文件让本模块
不依赖 serve 的 loader 实例，单测只需 mock 路径、不必起整条链路。

三个端点：
- GET /api/growth                 → 成长总览（章数/年龄/时间线/各章 summary+report/installed_skills 汇总）。
- GET /api/growth/chapters/{n}    → 第 n 章详情（传记正文 + 汇报 + 习得 skills）。严格校验 n。
- GET /api/growth/persona/{n}     → 第 n 章人格快照目录 data/growth/persona/chapter-n/ 各 md 内容。
  这是补 PR2 缺口的端点：现有 /api/persona 的守卫只认 persona_dir、读不到 data_dir 下的成长章目录；
  本端点直接读成长章目录，带自己的 `..` 遍历守卫。

成长未激活（默认）时 /api/growth 优雅回"空闲态"（无 state 文件不报 500），符合 R9 约束。
"""

from __future__ import annotations

import contextlib
import json
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sanshiliu.foundation.logging import get_logger
from sanshiliu.scheduler.growth_persona import (
    PERSONA_SECTION_FILES,
    chapter_persona_dir,
    growth_persona_root,
)
from sanshiliu.scheduler.growth_state import GrowthState, load_growth_state

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler

_logger = get_logger(__name__)


# ────────── 工具（与 api.py 同款，独立一份避免跨模块耦合） ──────────

def _write_json(req: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    req.send_response(status)
    req.send_header("Content-Type", "application/json; charset=utf-8")
    req.send_header("Content-Length", str(len(body)))
    req.send_header("Cache-Control", "no-store")
    req.end_headers()
    req.wfile.write(body)


def _safe(req: BaseHTTPRequestHandler, fn: Callable[[], None], where: str) -> None:
    try:
        fn()
    except Exception as exc:
        _logger.exception("growth api 处理失败", path=where, error=str(exc))
        with contextlib.suppress(Exception):
            _write_json(req, {"error": str(exc), "where": where}, status=500)


def _read_biography(memdir_dir: Path, chapter_no: int) -> str:
    """读第 N 章传记正文：memdir 里 write_memory_file 落的是 reference_growth-chapter-N_<ts>.md，
    文件名带时间戳，所以按前缀 glob、取最新一份；读不到返空串（不报错）。"""
    if not memdir_dir.is_dir():
        return ""
    # 前缀严格到 chapter-N_，避免 chapter-1 误匹配 chapter-10（下划线分隔时间戳）
    matches = sorted(memdir_dir.glob(f"reference_growth-chapter-{chapter_no}_*.md"))
    if not matches:
        return ""
    try:
        return matches[-1].read_text(encoding="utf-8")
    except OSError:
        return ""


def _chapter_to_dict(state: GrowthState, idx: int) -> dict[str, Any]:
    """把第 idx（0-based）章的 ChapterRecord 摊平成 dict；chapter_no 为 1-based 给前端。"""
    ch = state.chapters[idx]
    chapter_no = idx + 1
    return {
        "chapter_no": chapter_no,
        "age_range": ch.age_range,
        "summary": ch.summary,
        "report": ch.report,
        "installed_skills": list(ch.installed_skills),
        "created_at": ch.created_at,
        # 人格快照引用：dashboard 据此调 /api/growth/persona/{chapter_no} 取该章人格正文
        "persona_snapshot_ref": f"/api/growth/persona/{chapter_no}",
    }


# ────────── GET /api/growth ──────────

def make_growth_overview_handler(
    growth_state_path: Path,
    *,
    start_age: int,
    years_per_chapter: int,
    end_age: int,
    enabled: bool,
) -> Callable[[BaseHTTPRequestHandler], None]:
    """成长总览：current_chapter/age、时间线、各章 summary+report、installed_skills 汇总、enabled。

    enabled 是 serve 启动时的 growth_enabled（kill-switch / 调度入口在心跳模块）；成长从未跑过
    （无 state 文件）时 load_growth_state 返回按 config seed 的空闲态，照常出总览、不报 500。
    """
    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            state = load_growth_state(
                growth_state_path,
                start_age=start_age,
                years_per_chapter=years_per_chapter,
                end_age=end_age,
            )
            # installed_skills 跨章去重汇总（按出现顺序）
            seen: set[str] = set()
            all_skills: list[str] = []
            for ch in state.chapters:
                for sid in ch.installed_skills:
                    if sid not in seen:
                        seen.add(sid)
                        all_skills.append(sid)
            chapters = [_chapter_to_dict(state, i) for i in range(len(state.chapters))]
            end_age_eff = state.start_age + state.end_chapter * state.years_per_chapter
            payload = {
                "enabled": bool(enabled),
                "current_chapter": state.current_chapter,
                "age": state.age,
                "active_persona_chapter": state.active_persona_chapter,
                "start_age": state.start_age,
                "end_age": end_age_eff,
                "years_per_chapter": state.years_per_chapter,
                "end_chapter": state.end_chapter,
                "frozen": not state.can_advance(),
                # 时间线：start_age → end_age，标出当前年龄；前端画进度条
                "timeline": {
                    "start_age": state.start_age,
                    "end_age": end_age_eff,
                    "current_age": state.age,
                },
                "installed_skills": all_skills,
                "chapters": chapters,
            }
            _write_json(req, payload)

        _safe(req, _do, "/api/growth")

    return handler


# ────────── GET /api/growth/chapters/{n} ──────────

def _parse_chapter_no(path: str, *, segment: str) -> int | None:
    """从 /api/growth/{segment}/{n} 严格抽取 1-based 章号；形状不符 / 非纯数字 → None。

    segment 形如 "chapters" 或 "persona"。严格校验：恰好 4 段、第 4 段是纯数字且 > 0，
    挡掉 register_prefix 兜来的 /api/growth/foo/bar 等异形路径与 `..` 注入。
    """
    raw = path.split("?", 1)[0]
    parts = raw.strip("/").split("/")
    if len(parts) != 4 or parts[0] != "api" or parts[1] != "growth" or parts[2] != segment:
        return None
    token = urllib.parse.unquote(parts[3])
    # 只认 ASCII 十进制正整数：排除负号 / 小数点 / ".." / 空串，且必须 isascii——
    # str.isdigit() 对上标 "²"、阿拉伯-印度数字 "١"、全角 "１" 等也回 True，但 int() 会
    # 抛 ValueError（"²"）或解析成意外值，故用 isascii()+isdecimal() 锁死，保证 int() 必成功。
    if not (token.isascii() and token.isdecimal()):
        return None
    n = int(token)
    return n if n > 0 else None


def make_growth_chapter_handler(
    growth_state_path: Path,
    memdir_dir: Path,
    *,
    start_age: int,
    years_per_chapter: int,
    end_age: int,
) -> Callable[[BaseHTTPRequestHandler], None]:
    """第 n 章详情：narrative/传记正文（读 memdir）+ report + 习得 skills + persona 快照引用。"""
    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            n = _parse_chapter_no(req.path, segment="chapters")
            if n is None:
                _write_json(req, {"error": "invalid chapter number"}, status=400)
                return
            state = load_growth_state(
                growth_state_path,
                start_age=start_age,
                years_per_chapter=years_per_chapter,
                end_age=end_age,
            )
            # n 是 1-based；超出已完成章数 → 404（这一章还没长到）
            if n > len(state.chapters):
                _write_json(
                    req,
                    {"error": "chapter not found", "chapter_no": n, "completed": len(state.chapters)},
                    status=404,
                )
                return
            detail = _chapter_to_dict(state, n - 1)
            # 传记正文（next-chapter 输入那份 md 的全文，含习得/人格摘要）；读不到给空串
            detail["biography"] = _read_biography(memdir_dir, n)
            _write_json(req, detail)

        _safe(req, _do, "/api/growth/chapters")

    return handler


# ────────── GET /api/growth/persona/{n} ──────────

def make_growth_persona_handler(
    data_dir: Path,
) -> Callable[[BaseHTTPRequestHandler], None]:
    """第 n 章人格快照：读 data/growth/persona/chapter-n/ 下各 md（看每章人格演化）。

    自带遍历守卫：chapter_persona_dir 由整数 n 拼成（_parse_chapter_no 已保证纯数字），且
    .resolve() 后校验仍在 growth_persona_root 之下——双保险挡 `..`。这是 PR2 标记的缺口端点：
    /api/persona 的守卫绑 persona_dir、读不到 data_dir 下的成长章目录，故另起此端点直读成长章目录。
    """
    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            n = _parse_chapter_no(req.path, segment="persona")
            if n is None:
                _write_json(req, {"error": "invalid chapter number"}, status=400)
                return
            root = growth_persona_root(data_dir).resolve()
            ch_dir = chapter_persona_dir(data_dir, n)
            try:
                resolved = ch_dir.resolve()
            except OSError:
                _write_json(req, {"error": "not found", "chapter_no": n}, status=404)
                return
            # 双保险：解析后必须仍在成长人格根之下（_parse 已挡 ..，这里再兜一层）
            try:
                resolved.relative_to(root)
            except ValueError:
                _write_json(req, {"error": "invalid chapter dir"}, status=400)
                return
            if not resolved.is_dir():
                _write_json(req, {"error": "not found", "chapter_no": n}, status=404)
                return
            # 读该章人格目录下的每个 .md（identity/personality/beliefs/style/fewshot_short）
            files: list[dict[str, Any]] = []
            for p in sorted(resolved.glob("*.md")):
                if not p.is_file():
                    continue
                try:
                    body = p.read_text(encoding="utf-8")
                except OSError:
                    continue
                # 段落键（去 .md 后缀）：让前端能按 identity/style 等分组展示
                section = next(
                    (k for k, v in PERSONA_SECTION_FILES.items() if v == p.name),
                    p.stem,
                )
                files.append({
                    "name": p.name,
                    "section": section,
                    "body": body,
                    "chars": len(body),
                })
            _write_json(req, {
                "chapter_no": n,
                "dir": str(resolved),
                "files": files,
            })

        _safe(req, _do, "/api/growth/persona")

    return handler
