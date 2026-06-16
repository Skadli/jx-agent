"""抽卡平台 web 端点（设计 §6）：SSE 锻造流 + 卡册读 + 删卡 + 锻造锁 + 每日上限。

端点（注册见 runner.py；/api/* 统一受 DashboardAuth 保护）：
- POST   /api/gacha/draw                    抽一张新卡并同步锻满 → SSE 锻造流
- POST   /api/gacha/cards/{id}/forge        续锻到定格（创始卡 / error 卡重试用）→ SSE
- POST   /api/gacha/cards/{id}/rebirth      转生：该卡成为全渠道当前人格（在锻/无人格拒）
- POST   /api/gacha/rebirth/reset           一键回本源（不受 enabled 闸——逃生通道）
- GET    /api/gacha/active                  当前真身（激活指针解析 + 卡面摘要）
- GET    /api/gacha/cards                   卡册列表（卡面摘要，最新在前）
- GET    /api/gacha/cards/{id}              卡详情（全量 card.json + 汇总）
- GET    /api/gacha/cards/{id}/chapters/{n} 章详情（含传记全文）
- GET    /api/gacha/cards/{id}/persona/{n}  章人格快照（带 `..` 遍历双保险）
- DELETE /api/gacha/cards/{id}              删卡（创始卡拒删；在锻中的卡拒删）

与 /chat SSE 的三处关键差异（一次锻造 10-20 分钟，不是一次 LLM 调用）：
- **deadline 放宽到小时级**：只作 runaway 兜底，不是正常超时；
- **客户端断开锻造仍继续**：safe_write 失败只停止推流、不取消协程——每章落盘，
  断了用 GET 看进度、用 /forge 续锻（实际上协程会自己跑完）；
- **锻造锁生命周期 = 协程生命周期**：threading.Lock 在请求线程 acquire(blocking=False)
  （忙→409），在锻造协程 finally 里 release（threading.Lock 允许跨线程 release）——
  绝不能在 handler 线程退出时放锁，否则客户端断开后会出现两张卡并发锻造。

写端点（draw/forge/delete）受 settings.gacha_enabled 闸门（403）；读端点永远可用
（看历史卡不耗钱）。读 handler 的工厂风格 / 失败回 500 不抛 / 路径严格校验与 api_growth 一致。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import queue as q_mod
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sanshiliu.channels.web.responses import write_json as _write_json
from sanshiliu.channels.web.sse import format_event, format_heartbeat, safe_write
from sanshiliu.foundation.logging import get_logger
from sanshiliu.gacha.active import (
    ActivePointer,
    describe_active,
    resolve_active,
    resolve_persona_dir_for_card,
    save_active_pointer,
)
from sanshiliu.gacha.card_persona import PERSONA_SECTION_FILES, chapter_persona_dir
from sanshiliu.gacha.card_state import (
    ORIGIN_CARD_ID,
    biography_path,
    create_card,
    is_valid_card_id,
    load_card_state,
    persona_root,
)
from sanshiliu.gacha.forge_runner import ForgeChapterError, ForgeRunner
from sanshiliu.gacha.pool import (
    card_summary,
    delete_card,
    draws_today,
    draws_today_from_summaries,
    list_cards,
)
from sanshiliu.gacha.seeds import GENRES, draw_seed

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler

    from sanshiliu.identity.loader import PersonaLoader

_logger = get_logger(__name__)

# 锻满一张卡 ≈ 11 章 × 1-3 分钟 + 评级；deadline 只兜 runaway（模型挂死等），给 4 小时。
_FORGE_DEADLINE_SEC = 4 * 3600.0
_HEARTBEAT_INTERVAL_SEC = 15.0

# draw 请求体只有 genre/custom_prompt/creativity 三个小字段
_DRAW_BODY_LIMIT = 64 * 1024
_CUSTOM_PROMPT_MAX_CHARS = 2000


class ForgeGate:
    """全局锻造闸：同时只允许 1 张卡在锻，并记录在锻 card_id（删除守卫用）。

    threading.Lock 允许跨线程 release：请求线程 try_acquire（忙→409），锻造协程
    （事件循环线程）finally 里 release——锁生命周期跟协程走，客户端断开不提前放锁。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.current_card_id: str = ""

    def try_acquire(self, card_id: str) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        self.current_card_id = card_id
        return True

    def release(self) -> None:
        self.current_card_id = ""
        # 重复 release 是编程错误，但绝不能因此炸掉锻造协程的 finally
        with contextlib.suppress(RuntimeError):
            self._lock.release()


# ────────── 工具 ──────────


def _safe(req: BaseHTTPRequestHandler, fn: Callable[[], None], where: str) -> None:
    try:
        fn()
    except Exception as exc:
        _logger.exception("gacha api 处理失败", path=where, error=str(exc))
        with contextlib.suppress(Exception):
            _write_json(req, {"error": str(exc), "where": where}, status=500)


def _read_json_body(req: BaseHTTPRequestHandler, *, limit: int) -> dict[str, Any] | None:
    """读 JSON 请求体；无体 → {}；坏 Content-Length/超限/坏 JSON/非对象 → None（调用方回 400）。"""
    try:
        length = int(req.headers.get("Content-Length", "0") or "0")
    except ValueError:
        return None  # 畸形 Content-Length 是客户端错误，回 400 而不是炸成 500
    if length == 0:
        return {}
    if length < 0 or length > limit:
        return None
    try:
        raw = json.loads(req.rfile.read(length).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _path_parts(path: str) -> list[str]:
    return path.split("?", 1)[0].strip("/").split("/")


def _parse_chapter_no(token: str) -> int | None:
    """严格抽取 1-based 章号：仅 ASCII 十进制正整数（挡 `..`/全角数字/上标等异形）。"""
    if not (token.isascii() and token.isdecimal()):
        return None
    n = int(token)
    return n if n > 0 else None


# ────────── SSE 锻造流（draw / forge 共用） ──────────


def _stream_forge(
    req: BaseHTTPRequestHandler,
    *,
    runner: ForgeRunner,
    gate: ForgeGate,
    loop: asyncio.AbstractEventLoop,
    card_id: str,
    intro_events: list[tuple[str, dict[str, Any]]],
) -> None:
    """把一次锻造（已持有 gate）以 SSE 推给客户端；本函数保证 gate 最终被释放。

    调用前提：gate 已被本请求 try_acquire。**排上协程之前的任何异常**（写响应头时对端
    已断 → ConnectionAborted、loop 已关等）都由本函数收回释放——否则闸被一个夭折的请求
    永久占住，后续 draw/forge 全部 409 直到重启。协程一旦排上，释放责任移交给协程的
    finally（客户端断开锻造继续，锁不提前放）。
    """
    sse_q: q_mod.Queue[Any] = q_mod.Queue()
    sentinel = object()

    async def _on_event(ev: dict[str, Any]) -> None:
        sse_q.put(ev)

    async def _produce() -> None:
        try:
            await runner.forge_card(card_id, on_event=_on_event)
        except ForgeChapterError:
            # error 事件已由 runner emit（含卡不存在路径）、卡已标 error；这里只负责收尾
            pass
        except Exception as exc:
            _logger.exception("锻造协程异常", card_id=card_id, error=str(exc))
            sse_q.put(
                {
                    "type": "error",
                    "card_id": card_id,
                    "message": f"{type(exc).__name__}: {exc}",
                }
            )
        finally:
            gate.release()
            sse_q.put(sentinel)

    scheduled = False
    try:
        req.send_response(200)
        req.send_header("Content-Type", "text/event-stream; charset=utf-8")
        req.send_header("Cache-Control", "no-cache, no-transform")
        req.send_header("Connection", "close")
        req.send_header("X-Accel-Buffering", "no")
        req.end_headers()

        for ev_name, payload in intro_events:
            safe_write(
                req.wfile,
                format_event(json.dumps(payload, ensure_ascii=False, default=str), event=ev_name),
            )

        asyncio.run_coroutine_threadsafe(_produce(), loop)
        scheduled = True
    finally:
        if not scheduled:
            # 协程没排上（响应头写挂/loop 已关）：释放责任收回本线程，异常继续上抛给 _safe
            gate.release()

    deadline = time.monotonic() + _FORGE_DEADLINE_SEC
    last_beat = time.monotonic()
    try:
        while True:
            if time.monotonic() > deadline:
                safe_write(req.wfile, format_event("forge deadline exceeded", event="error"))
                _logger.error("锻造 SSE 触达 deadline 兜底（锻造协程不取消）", card_id=card_id)
                break
            if time.monotonic() - last_beat > _HEARTBEAT_INTERVAL_SEC:
                if not safe_write(req.wfile, format_heartbeat()):
                    _logger.info("锻造 SSE 客户端断开（锻造继续，可 GET 看进度）", card_id=card_id)
                    break
                last_beat = time.monotonic()
            try:
                item = sse_q.get(timeout=0.5)
            except q_mod.Empty:
                continue
            if item is sentinel:
                break
            ev_type = str(item.get("type") or "event") if isinstance(item, dict) else "event"
            data = json.dumps(item, ensure_ascii=False, default=str)
            if not safe_write(req.wfile, format_event(data, event=ev_type)):
                _logger.info("锻造 SSE 客户端断开（锻造继续，可 GET 看进度）", card_id=card_id)
                break
            last_beat = time.monotonic()
    finally:
        # 不等协程：断开后锻造在事件循环里继续到定格，每章已落盘
        req.close_connection = True


# ────────── POST /api/gacha/draw ──────────


def make_gacha_draw_handler(
    runner: ForgeRunner,
    gate: ForgeGate,
    loop: asyncio.AbstractEventLoop,
    *,
    gacha_root: Path,
    enabled: bool,
    start_age: int,
    years_per_chapter: int,
    end_age: int,
    birth_year: int,
    daily_draw_limit: int,
) -> Callable[[BaseHTTPRequestHandler], None]:
    """抽卡：校验额度/参数 → 随机种子建卡 → SSE 同步锻满（决策 #1）。"""

    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            if not enabled:
                _write_json(
                    req,
                    {"error": "抽卡平台未启用（SANSHILIU_GACHA_ENABLED=false）"},
                    status=403,
                )
                return
            body = _read_json_body(req, limit=_DRAW_BODY_LIMIT)
            if body is None:
                _write_json(req, {"error": "invalid JSON body"}, status=400)
                return
            genre = body.get("genre")
            if genre is not None and not isinstance(genre, str):
                _write_json(req, {"error": "genre 必须是字符串"}, status=400)
                return
            custom_prompt = body.get("custom_prompt", "")
            if not isinstance(custom_prompt, str) or len(custom_prompt) > _CUSTOM_PROMPT_MAX_CHARS:
                _write_json(
                    req,
                    {"error": f"custom_prompt 必须是 ≤{_CUSTOM_PROMPT_MAX_CHARS} 字的字符串"},
                    status=400,
                )
                return
            creativity_raw = body.get("creativity")
            creativity: float | None = None
            if creativity_raw is not None:
                if isinstance(creativity_raw, bool) or not isinstance(creativity_raw, int | float):
                    _write_json(req, {"error": "creativity 必须是 0-2 的数字"}, status=400)
                    return
                creativity = min(max(float(creativity_raw), 0.0), 2.0)

            if daily_draw_limit > 0 and draws_today(gacha_root) >= daily_draw_limit:
                _write_json(
                    req,
                    {
                        "error": f"今日抽卡已达上限 {daily_draw_limit} 张（成本护栏）",
                        "limit": daily_draw_limit,
                    },
                    status=429,
                )
                return
            if not gate.try_acquire("(drawing)"):
                _write_json(
                    req,
                    {"error": "另一张卡正在锻造中", "forging": gate.current_card_id},
                    status=409,
                )
                return
            try:
                seed = draw_seed(
                    genre=genre,
                    custom_prompt=custom_prompt,
                    creativity=creativity,
                    birth_year=birth_year,
                )
                card = create_card(
                    gacha_root,
                    seed,
                    start_age=start_age,
                    years_per_chapter=years_per_chapter,
                    end_age=end_age,
                )
            except Exception:
                gate.release()
                raise
            gate.current_card_id = card.card_id
            intro: list[tuple[str, dict[str, Any]]] = [
                ("card_created", card_summary(card)),
            ]
            _stream_forge(
                req, runner=runner, gate=gate, loop=loop, card_id=card.card_id, intro_events=intro
            )

        _safe(req, _do, "/api/gacha/draw")

    return handler


# ────────── POST /api/gacha/cards/{id}/forge ──────────


def make_gacha_card_post_handler(
    runner: ForgeRunner,
    gate: ForgeGate,
    loop: asyncio.AbstractEventLoop,
    *,
    gacha_root: Path,
    enabled: bool,
    persona_loader: PersonaLoader,
) -> Callable[[BaseHTTPRequestHandler], None]:
    """卡上的两个动作：/api/gacha/cards/{id}/forge（续锻 SSE）与 /{id}/rebirth（转生）。

    转生（设计决策 #4/#8）：写 active.json 指向该卡（follow 模式，卡再长真身跟着长）+
    PersonaLoader.invalidate() 即刻生效，**全渠道**（web/REPL/微信）下一轮就是新人格。
    二次确认在 dashboard 层（PR4）；API 层守卫：在锻中的卡拒转（人格半生不熟）、
    没有任何可用人格章的卡拒转。
    """

    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            parts = _path_parts(req.path)
            if (
                len(parts) != 5
                or parts[:3] != ["api", "gacha", "cards"]
                or parts[4] not in ("forge", "rebirth")
            ):
                _write_json(req, {"error": "not found"}, status=404)
                return
            card_id = parts[3]
            action = parts[4]
            if not is_valid_card_id(card_id):
                _write_json(req, {"error": "invalid card_id"}, status=400)
                return
            if not enabled:
                _write_json(
                    req,
                    {"error": "抽卡平台未启用（SANSHILIU_GACHA_ENABLED=false）"},
                    status=403,
                )
                return
            state = load_card_state(gacha_root, card_id)
            if state is None:
                _write_json(req, {"error": "卡不存在", "card_id": card_id}, status=404)
                return
            # 消化掉可能的请求体（forge/rebirth 当前协议都无参数），保持连接干净
            _read_json_body(req, limit=_DRAW_BODY_LIMIT)

            if action == "rebirth":
                _do_rebirth(req, gacha_root, gate, persona_loader, card_id=card_id)
                return

            if not gate.try_acquire(card_id):
                _write_json(
                    req,
                    {"error": "另一张卡正在锻造中", "forging": gate.current_card_id},
                    status=409,
                )
                return
            intro: list[tuple[str, dict[str, Any]]] = [
                ("forge_resume", card_summary(state)),
            ]
            _stream_forge(
                req, runner=runner, gate=gate, loop=loop, card_id=card_id, intro_events=intro
            )

        _safe(req, _do, "/api/gacha/cards/{id}/forge|rebirth")

    return handler


def _do_rebirth(
    req: BaseHTTPRequestHandler,
    gacha_root: Path,
    gate: ForgeGate,
    persona_loader: PersonaLoader,
    *,
    card_id: str,
) -> None:
    """执行转生：守卫 → 写指针（follow 模式）→ invalidate 热生效 → 回当前激活态。"""
    if gate.current_card_id == card_id:
        _write_json(req, {"error": "该卡正在锻造中，等定格后再转生"}, status=409)
        return
    if resolve_persona_dir_for_card(gacha_root, card_id) is None:
        _write_json(
            req,
            {"error": "该卡还没有任何可用人格章（先锻造至少一章）", "card_id": card_id},
            status=409,
        )
        return
    save_active_pointer(gacha_root, ActivePointer(card_id=card_id, chapter=None))
    persona_loader.invalidate()
    active = describe_active(gacha_root)
    _logger.info("转生完成（全渠道生效）", card_id=card_id, active=active)
    _write_json(req, {"ok": True, "card_id": card_id, "active": active})


# ────────── POST /api/gacha/rebirth/reset + GET /api/gacha/active ──────────


def make_gacha_rebirth_reset_handler(
    gacha_root: Path,
    persona_loader: PersonaLoader,
) -> Callable[[BaseHTTPRequestHandler], None]:
    """一键回本源：指针指回创始卡 origin。

    **不受 gacha_enabled 闸门**——这是逃生通道：转生后把平台关了，也必须能把人格收回
    三十六贱笑（kill-switch 不该把人困在某张卡里）。
    """

    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            save_active_pointer(gacha_root, ActivePointer(card_id=ORIGIN_CARD_ID, chapter=None))
            persona_loader.invalidate()
            active = describe_active(gacha_root)
            _logger.info("已回滚到创始卡", active=active)
            _write_json(req, {"ok": True, "active": active})

        _safe(req, _do, "/api/gacha/rebirth/reset")

    return handler


def make_gacha_genres_handler() -> Callable[[BaseHTTPRequestHandler], None]:
    """卡池世界类型表（锻造台选型用）；纯静态，免得前端复刻 seeds.py。"""
    payload = {
        "genres": [
            {"id": g.id, "label": g.label, "icon": g.icon, "triggers": list(g.triggers)}
            for g in GENRES
        ]
    }

    def handler(req: BaseHTTPRequestHandler) -> None:
        _safe(req, lambda: _write_json(req, payload), "/api/gacha/genres")

    return handler


def make_gacha_active_handler(
    gacha_root: Path,
) -> Callable[[BaseHTTPRequestHandler], None]:
    """当前真身：激活指针解析结果 + 该卡卡面摘要（dashboard 顶栏「当前真身」用）。"""

    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            # 用 resolve_active 一次拿到 state，别 describe 完又 load 一遍同一本 card.json
            source, pointer, state, resolved = resolve_active(gacha_root)
            active = {
                "source": source,
                "card_id": state.card_id if state is not None else None,
                "pinned_chapter": pointer.chapter
                if (source == "pointer" and pointer is not None)
                else None,
                "resolved_chapter": resolved[1] if resolved is not None else None,
            }
            card = card_summary(state) if state is not None else None
            _write_json(req, {"active": active, "card": card})

        _safe(req, _do, "/api/gacha/active")

    return handler


# ────────── GET /api/gacha/cards ──────────


def make_gacha_cards_list_handler(
    gacha_root: Path,
    *,
    enabled: bool,
    daily_draw_limit: int,
) -> Callable[[BaseHTTPRequestHandler], None]:
    """卡册列表 + 平台状态（enabled / 今日剩余额度），前端据此渲染锻造台可用性。"""

    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            cards = list_cards(gacha_root)
            # 摘要已在手，别再整目录重扫一遍算额度
            today = draws_today_from_summaries(cards)
            _write_json(
                req,
                {
                    "enabled": enabled,
                    "count": len(cards),
                    "draws_today": today,
                    "daily_draw_limit": daily_draw_limit,
                    "cards": cards,
                },
            )

        _safe(req, _do, "/api/gacha/cards")

    return handler


# ────────── GET /api/gacha/cards/{id}[/chapters/{n}|/persona/{n}] ──────────


def make_gacha_card_get_handler(
    gacha_root: Path,
) -> Callable[[BaseHTTPRequestHandler], None]:
    """卡详情 / 章详情（含传记全文）/ 章人格快照；路径形状严格校验。"""

    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            parts = _path_parts(req.path)
            if len(parts) < 4 or parts[:3] != ["api", "gacha", "cards"]:
                _write_json(req, {"error": "not found"}, status=404)
                return
            card_id = parts[3]
            if not is_valid_card_id(card_id):
                _write_json(req, {"error": "invalid card_id"}, status=400)
                return
            state = load_card_state(gacha_root, card_id)
            if state is None:
                _write_json(req, {"error": "卡不存在", "card_id": card_id}, status=404)
                return

            if len(parts) == 4:
                _write_card_detail(req, state_dict=state.to_dict(), summary=card_summary(state))
                return
            if len(parts) == 6 and parts[4] == "chapters":
                _write_chapter_detail(req, gacha_root, state_chapters=state, token=parts[5])
                return
            if len(parts) == 6 and parts[4] == "persona":
                _write_persona_snapshot(req, gacha_root, card_id=card_id, token=parts[5])
                return
            _write_json(req, {"error": "not found"}, status=404)

        _safe(req, _do, "/api/gacha/cards/{id}")

    return handler


def _write_card_detail(
    req: BaseHTTPRequestHandler, *, state_dict: dict[str, Any], summary: dict[str, Any]
) -> None:
    """卡详情 = 全量 card.json + 卡面摘要字段（end_age/skill_count 等前端免算）。"""
    payload = dict(state_dict)
    payload.update(
        {
            "end_age": summary["end_age"],
            "skill_count": summary["skill_count"],
            "is_origin": summary["is_origin"],
        }
    )
    _write_json(req, payload)


def _write_chapter_detail(
    req: BaseHTTPRequestHandler, gacha_root: Path, *, state_chapters: Any, token: str
) -> None:
    n = _parse_chapter_no(token)
    if n is None:
        _write_json(req, {"error": "invalid chapter number"}, status=400)
        return
    chapters = state_chapters.chapters
    if n > len(chapters):
        _write_json(
            req,
            {"error": "chapter not found", "chapter_no": n, "completed": len(chapters)},
            status=404,
        )
        return
    ch = chapters[n - 1]
    biography = ""
    bio_path = biography_path(gacha_root, state_chapters.card_id, n)
    if bio_path.is_file():
        try:
            biography = bio_path.read_text(encoding="utf-8")
        except OSError:
            biography = ""
    _write_json(
        req,
        {
            "chapter_no": n,
            "age_range": ch.age_range,
            "summary": ch.summary,
            "report": ch.report,
            "installed_skills": list(ch.installed_skills),
            "created_at": ch.created_at,
            "biography": biography,
        },
    )


def _write_persona_snapshot(
    req: BaseHTTPRequestHandler, gacha_root: Path, *, card_id: str, token: str
) -> None:
    """第 n 章人格快照：读 cards/<id>/persona/chapter-n/ 各 md（含 chapter-0 出生底版）。

    与 api_growth 同款双保险：章号已锁纯数字，resolve 后再校验仍在本卡 persona 根之下。
    """
    # chapter-0（出生底版）也允许看：单独放行 0
    if not (token.isascii() and token.isdecimal()):
        _write_json(req, {"error": "invalid chapter number"}, status=400)
        return
    n = int(token)
    root = persona_root(gacha_root, card_id).resolve()
    ch_dir = chapter_persona_dir(persona_root(gacha_root, card_id), n)
    try:
        resolved = ch_dir.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        _write_json(req, {"error": "invalid chapter dir"}, status=400)
        return
    if not resolved.is_dir():
        _write_json(req, {"error": "not found", "chapter_no": n}, status=404)
        return
    files: list[dict[str, Any]] = []
    for p in sorted(resolved.glob("*.md")):
        if not p.is_file():
            continue
        try:
            body = p.read_text(encoding="utf-8")
        except OSError:
            continue
        section = next(
            (k for k, v in PERSONA_SECTION_FILES.items() if v == p.name),
            p.stem,
        )
        files.append({"name": p.name, "section": section, "body": body, "chars": len(body)})
    _write_json(req, {"card_id": card_id, "chapter_no": n, "files": files})


# ────────── DELETE /api/gacha/cards/{id} ──────────


def make_gacha_card_delete_handler(
    gacha_root: Path,
    gate: ForgeGate,
    *,
    enabled: bool,
) -> Callable[[BaseHTTPRequestHandler], None]:
    """删卡：创始卡拒删（403）、有锻造在跑拒删（409）、不存在 404。

    删除**持闸执行**而不是只看 current_card_id：check-then-act 之间挤进来一个 forge
    会边删边写、把删掉的卡复活。删卡是毫秒级操作，独占闸的代价可忽略。
    """

    def handler(req: BaseHTTPRequestHandler) -> None:
        def _do() -> None:
            parts = _path_parts(req.path)
            if len(parts) != 4 or parts[:3] != ["api", "gacha", "cards"]:
                _write_json(req, {"error": "not found"}, status=404)
                return
            card_id = parts[3]
            if not enabled:
                _write_json(
                    req,
                    {"error": "抽卡平台未启用（SANSHILIU_GACHA_ENABLED=false）"},
                    status=403,
                )
                return
            if not gate.try_acquire(f"(deleting:{card_id})"):
                _write_json(
                    req,
                    {"error": "有卡正在锻造中，稍后再删", "forging": gate.current_card_id},
                    status=409,
                )
                return
            try:
                ok, reason = delete_card(gacha_root, card_id)
            finally:
                gate.release()
            if ok:
                _write_json(req, {"ok": True, "card_id": card_id})
                return
            status = 403 if "创始卡" in reason else 404 if "不存在" in reason else 400
            _write_json(req, {"error": reason, "card_id": card_id}, status=status)

        _safe(req, _do, "/api/gacha/cards/{id} DELETE")

    return handler
