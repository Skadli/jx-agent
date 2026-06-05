"""做梦执行器；定时器闸门通过后被调用，实际让 engine 跑一轮 dream skill。

设计要点：
- **跨通道**：扫 <data_dir>/sessions/*.jsonl 取所有 channel（repl/web/wechat）的最近会话；
  jsonl 文件名即 session_id，再到 sqlite sessions 表富集 channel / last_active_at。
- **合成 session**：每次做梦新建独立 Session(channel="scheduler", user_id="dream-scheduler")，
  避免污染真实用户会话历史；做梦本身也会写一条 sessions 表记录，dashboard 可追溯。
- **材料前置注入**：把跨通道素材拼进 user_text 而不是让 LLM 自己调 LoadMemory——
  LoadMemory("recent") 只看当前 session 的 channel+user_id，对合成 session 无效。
- **错误不冒泡**：所有失败都吞掉记日志；定时器后台任务不能因为一次做梦失败而退出。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sanshiliu.engine.session import Session
from sanshiliu.foundation.logging import get_logger
from sanshiliu.scheduler.dream_log import append_dream_record

if TYPE_CHECKING:
    from sanshiliu.engine.loop import ConversationEngine
    from sanshiliu.storage.db import Database

_logger = get_logger(__name__)

_SCHEDULER_CHANNEL = "scheduler"
_SCHEDULER_USER_ID = "dream-scheduler"


class DreamRunner:
    """收材料 + 拼 prompt + 跑 engine 一轮；可直接当 OnDueCallback 用（__call__ 实现匹配签名）。"""

    def __init__(
        self,
        *,
        engine: ConversationEngine,
        db: Database | None,
        sessions_dir: Path,
        memdir_dir: Path | None = None,
        dream_log_path: Path | None = None,
        max_sessions: int = 8,
        max_msgs_per_session: int = 6,
        max_chars_per_msg: int = 400,
    ) -> None:
        self._engine = engine
        self._db = db
        self._sessions_dir = sessions_dir
        # memdir_dir：做梦产物（SaveMemory 写的反思条目）落点；用"做梦前/后目录 diff"记账本次
        # 写了哪些记忆（parse-free，目录是真相源）。缺它（单测/旧调用点）则 saved 记空，不报错。
        self._memdir_dir = memdir_dir
        # dream_log_path：做梦历史日志落点（<data_dir>/dream-log.json）；每次 ok/skipped/error 都
        # 追加一条，供心跳页回看。缺它则不记历史（只记日志），不影响做梦本身。
        self._dream_log_path = dream_log_path
        self._max_sessions = max_sessions
        self._max_msgs = max_msgs_per_session
        self._max_chars = max_chars_per_msg

    async def __call__(self, new_session_count: int, last_dream_ts: float) -> str:
        """跑一次做梦；返回一句人话结果（on_due 据此写 heartbeat last_message）。

        三态都**留痕**到 dream-log（#无法查看历史）：收集失败/engine 失败=error、0 材料=skipped、
        完成=ok（带摘要 + memdir 前后 diff 出的本次写入记忆）。错误仍不冒泡（后台任务不能因一次
        做梦失败退出），但不再"无声蒸发"——每次都进日志、可在心跳页回看。
        """
        try:
            materials = await self._collect_materials(last_dream_ts)
        except Exception as exc:
            _logger.error("收集做梦材料失败", error=str(exc))
            self._log_dream(
                status="error",
                detail=f"收集材料失败：{exc}",
                new_session_count=new_session_count,
                materials_count=0,
            )
            return "做梦失败：收集材料异常"

        if not materials:
            _logger.warning("dream runner 收到 0 条材料，跳过")
            self._log_dream(
                status="skipped",
                detail="无新增对话素材",
                new_session_count=new_session_count,
                materials_count=0,
            )
            return "跳过做梦：无新增素材"

        prompt = self._build_prompt(materials, new_session_count)
        session = Session.new(channel=_SCHEDULER_CHANNEL, user_id=_SCHEDULER_USER_ID)
        _logger.info(
            "dream runner 开始执行",
            scheduler_session=session.session_id,
            materials_count=len(materials),
            prompt_chars=len(prompt),
        )
        memdir_before = self._memdir_snapshot()
        try:
            result = await self._engine.complete_turn(session, prompt)
        except Exception as exc:
            _logger.error(
                "engine.complete_turn 失败",
                error=str(exc),
                scheduler_session=session.session_id,
            )
            self._log_dream(
                status="error",
                detail=f"engine.complete_turn 失败：{exc}",
                new_session_count=new_session_count,
                materials_count=len(materials),
                session_id=session.session_id,
            )
            return "做梦失败：engine 异常"

        content = result.content if isinstance(result.content, str) else ""
        saved = sorted(self._memdir_snapshot() - memdir_before)
        _logger.info(
            "dream runner 完成",
            scheduler_session=session.session_id,
            assistant_chars=len(content),
            saved_memories=saved,
        )
        self._log_dream(
            status="ok",
            detail="",
            new_session_count=new_session_count,
            materials_count=len(materials),
            session_id=session.session_id,
            summary=content[:280],
            saved_memories=saved,
        )
        tail = f"，写入 {len(saved)} 条记忆" if saved else ""
        return f"做梦完成（{len(materials)} 条素材{tail}）"

    def _memdir_snapshot(self) -> set[str]:
        """快照 memdir 下 *.md 文件名集合；用于 diff 出本次做梦写入的记忆。缺目录则空集。"""
        if self._memdir_dir is None or not self._memdir_dir.is_dir():
            return set()
        try:
            return {p.name for p in self._memdir_dir.glob("*.md")}
        except OSError:
            return set()

    def _log_dream(self, **record: Any) -> None:
        """追加一条做梦历史（best-effort）；缺 dream_log_path（单测/旧调用点）则跳过。"""
        if self._dream_log_path is None:
            return
        record.setdefault("ts", time.time())
        append_dream_record(self._dream_log_path, record)

    async def _collect_materials(self, since_ts: float) -> list[dict[str, Any]]:
        """扫 sessions_dir 取 mtime > since_ts 的 jsonl，按 mtime 倒序取最多 max_sessions 个。

        每条素材 dict 形如：
            {"session_id": "...", "channel": "...", "compact_summary": "...", "messages": [...]}
        """
        if not self._sessions_dir.is_dir():
            return []

        # 按 mtime 倒序排，取最新的 max_sessions 个
        candidates: list[tuple[float, Path]] = []
        for f in self._sessions_dir.glob("*.jsonl"):
            try:
                mt = f.stat().st_mtime
            except OSError:
                continue
            if mt > since_ts:
                candidates.append((mt, f))
        candidates.sort(reverse=True)
        candidates = candidates[: self._max_sessions]

        materials: list[dict[str, Any]] = []
        for _mt, path in candidates:
            sid = path.stem
            meta: dict[str, Any] = {"channel": "?", "user_id": None, "compact_summary": ""}
            if self._db is not None:
                try:
                    row = await self._db.get_session(sid)
                    if row is not None:
                        meta["channel"] = row.get("channel") or "?"
                        meta["user_id"] = row.get("user_id")
                        meta["compact_summary"] = row.get("compact_summary") or ""
                except Exception as exc:
                    _logger.warning("get_session 失败，仅用 jsonl", session_id=sid, error=str(exc))

            msgs = self._read_recent_messages(path)
            if not msgs and not meta["compact_summary"]:
                # 一条都没读出来，跳过
                continue
            materials.append(
                {
                    "session_id": sid,
                    "channel": meta["channel"],
                    "user_id": meta["user_id"],
                    "compact_summary": meta["compact_summary"],
                    "messages": msgs,
                }
            )
        return materials

    def _read_recent_messages(self, path: Path) -> list[dict[str, str]]:
        """读 jsonl 最后 max_msgs 条 user/assistant 文本消息；其他 role/工具消息跳过。"""
        try:
            with path.open("r", encoding="utf-8") as f:
                rows = f.readlines()
        except OSError:
            return []

        recent: list[dict[str, str]] = []
        # 倒着扫，凑够 max_msgs 就停
        for line in reversed(rows):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            role = rec.get("role")
            if role not in ("user", "assistant"):
                continue
            text = _extract_text(rec.get("content"))
            if not text:
                continue
            if len(text) > self._max_chars:
                text = text[: self._max_chars] + "…"
            recent.append({"role": role, "text": text})
            if len(recent) >= self._max_msgs:
                break
        recent.reverse()  # 按时间正序返
        return recent

    def _build_prompt(self, materials: list[dict[str, Any]], new_session_count: int) -> str:
        lines: list[str] = [
            f"现在是夜里 {new_session_count} 个新对话累积之后的固定做梦时间。",
            "请按 Skill(dream) 协议完整做一次梦——读 dream skill 正文，按六步走，",
            "最终用 SaveMemory 写 memdir（reference 档案 + 可选 feedback 洞察）。",
            "如果 dream 协议判断出现了专业知识能力缺口，只能先用 Skill(skill-finder) 搜索评估，",
            "再按需用 Skill(skill-installer) 安装现成 skill；禁止本地创建 SKILL.md。",
            "",
            "**重要**：以下是跨所有通道（repl / web / wechat）最近的对话素材。",
            "不要再调 LoadMemory 取 recent——那只看你自己 channel=scheduler 的历史（空的）。",
            "直接用下面这些材料做梦：",
            "",
        ]
        for i, m in enumerate(materials, 1):
            header = f"==== 素材 #{i} · channel={m['channel']}"
            if m.get("user_id"):
                header += f" · user={m['user_id']}"
            header += f" · session={m['session_id'][:8]} ===="
            lines.append(header)
            if m["compact_summary"]:
                lines.append(f"[compact_summary] {m['compact_summary']}")
            for msg in m["messages"]:
                tag = "用户" if msg["role"] == "user" else "助手"
                lines.append(f"{tag}: {msg['text']}")
            lines.append("")
        return "\n".join(lines)


def _extract_text(content: Any) -> str:
    """ChatMessage.content 可为 str 或 list[dict]（多模态）；只取文本部分拼起来。"""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                t = item.get("type")
                if t == "text" and isinstance(item.get("text"), str):
                    parts.append(item["text"])
        return "\n".join(parts).strip()
    return ""
