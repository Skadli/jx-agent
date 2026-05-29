"""memdir consolidate；LLM 喂全量 → JSON diff → 用户确认 → 落盘。

只由 REPL slash 命令 `/memory consolidate` 触发（PRD ADR-A：避免 LLM 自动跑破坏性操作）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sanshiliu.foundation.errors import ConfigError, LLMError
from sanshiliu.foundation.frontmatter import parse
from sanshiliu.foundation.logging import get_logger
from sanshiliu.llm.client import LLMClient
from sanshiliu.llm.router import LLMRouter
from sanshiliu.memory.longterm.memdir import MemdirLoader
from sanshiliu.memory.types import MemoryEntry

_logger = get_logger(__name__)

_CONSOLIDATE_FILE = "memory_consolidate.md"
# 单次 LLM 最多接受的变更条数（merge + delete + rewrite 总和）；超出截断
_MAX_OPS = 10
# MEMORY.md 文件名固定（与 memdir.py 保持一致）
_INDEX_FILE = "MEMORY.md"

# 索引行格式：`- [name](file.md) — desc`
_NEW_INDEX_RE = re.compile(r"^- \[([^\]]+)\]\(")
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


def load_consolidate_instruction(prompts_dir: Path) -> str:
    """读 prompts/memory_consolidate.md；不存在抛 ConfigError 含字段名。"""
    path = prompts_dir / _CONSOLIDATE_FILE
    if not path.is_file():
        raise ConfigError(
            f"缺少 {_CONSOLIDATE_FILE}：{path}\n  解决：建立 prompts/{_CONSOLIDATE_FILE}",
        )
    return path.read_text(encoding="utf-8").strip()


@dataclass(frozen=True)
class MergeOp:
    """合并操作：保留 keep 文件 + 用 new_body 重写；删除 drop 列表中的每个文件。"""

    keep: str
    drop: list[str]
    new_body: str


@dataclass(frozen=True)
class DeleteOp:
    """删除单条记忆 + 同步删 MEMORY.md 中对应索引行。"""

    name: str
    reason: str


@dataclass(frozen=True)
class RewriteOp:
    """保持 frontmatter 不变、只替换 body；MEMORY.md 索引不动（name 没变）。"""

    name: str
    new_body: str


@dataclass(frozen=True)
class ConsolidateDiff:
    """LLM 给出的 diff；apply 前应展示给用户确认。"""

    merge: list[MergeOp] = field(default_factory=list)
    delete: list[DeleteOp] = field(default_factory=list)
    rewrite: list[RewriteOp] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_ops(self) -> int:
        return len(self.merge) + len(self.delete) + len(self.rewrite)

    @property
    def is_empty(self) -> bool:
        return self.total_ops == 0


@dataclass
class ConsolidateResult:
    """apply 后的统计；errors 含每项失败的原因（不阻塞其余项）。"""

    merged_count: int = 0
    deleted_count: int = 0
    rewritten_count: int = 0
    errors: list[str] = field(default_factory=list)


def _parse_json_object(text: str) -> dict[str, Any]:
    """LLM 可能返裸 JSON 也可能裹 markdown fence；剥两层取对象。坏 JSON 抛 ValueError。"""
    cleaned = text.strip()
    m = _JSON_FENCE_RE.search(cleaned)
    if m:
        cleaned = m.group(1)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"consolidate JSON 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"consolidate JSON 顶层必须是 object，实际：{type(data).__name__}")
    return data


def _coerce_merge(item: Any, valid_names: set[str]) -> MergeOp | None:
    if not isinstance(item, dict):
        return None
    keep = item.get("keep")
    drop_raw = item.get("drop")
    new_body = item.get("new_body")
    if not isinstance(keep, str) or not isinstance(drop_raw, list) or not isinstance(new_body, str):
        return None
    drop = [d for d in drop_raw if isinstance(d, str) and d]
    if not drop or keep not in valid_names:
        return None
    # 所有 drop 必须真实存在；任何一个不存在则整条无效
    if any(d not in valid_names for d in drop):
        return None
    # keep 不能同时出现在 drop 里
    if keep in drop:
        return None
    return MergeOp(keep=keep.strip(), drop=[d.strip() for d in drop], new_body=new_body.strip())


def _coerce_delete(item: Any, valid_names: set[str]) -> DeleteOp | None:
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    reason = item.get("reason")
    if not isinstance(name, str) or not isinstance(reason, str):
        return None
    if name not in valid_names:
        return None
    if not reason.strip():
        return None
    return DeleteOp(name=name.strip(), reason=reason.strip())


def _coerce_rewrite(item: Any, valid_names: set[str]) -> RewriteOp | None:
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    new_body = item.get("new_body")
    if not isinstance(name, str) or not isinstance(new_body, str):
        return None
    if name not in valid_names:
        return None
    if not new_body.strip():
        return None
    return RewriteOp(name=name.strip(), new_body=new_body.strip())


def _dump_entries_for_llm(entries: list[MemoryEntry]) -> str:
    """把全集 entries 序列化成 JSON 数组喂给 LLM。"""
    payload = [
        {
            "name": e.name,
            "type": e.memory_type,
            "apply": e.apply,
            "description": e.description,
            "body": e.body,
            "confidence": e.confidence,
            "source": e.source,
            "protected": e.protected,
        }
        for e in entries
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _index_line_matches_name(line: str, name: str) -> bool:
    """判断 MEMORY.md 中某一行是否对应给定 name（新索引格式 `- [name](file.md)`）。"""
    m = _NEW_INDEX_RE.match(line)
    if m:
        return m.group(1).strip() == name
    return False


def _rewrite_body_in_file(path: Path, new_body: str) -> None:
    """读取 md → 保留 frontmatter → 替换 body → 写回。"""
    text = path.read_text(encoding="utf-8")
    parsed = parse(text)
    # parse 后正文不一定带 frontmatter；如果没有 frontmatter，整文件覆盖 new_body 即可
    lines = text.splitlines(keepends=False)
    if not parsed.frontmatter or not lines or lines[0].strip() != "---":
        path.write_text(new_body.strip() + "\n", encoding="utf-8")
        return
    # 找到闭合 ---，保留 frontmatter 整段（含两个 ---），把后面替换为 new_body
    end_idx = -1
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = i
            break
    if end_idx < 0:
        # 不应该走到（parse 也会拒绝），保险起见整覆盖
        path.write_text(new_body.strip() + "\n", encoding="utf-8")
        return
    fm_block = "\n".join(lines[: end_idx + 1])
    path.write_text(fm_block + "\n\n" + new_body.strip() + "\n", encoding="utf-8")


def _remove_index_lines(index_path: Path, names_to_remove: set[str]) -> None:
    """从 MEMORY.md 中删除对应 name 的索引行；其余行（含注释/空行）保留。"""
    if not names_to_remove:
        return
    if not index_path.is_file():
        return
    raw = index_path.read_text(encoding="utf-8")
    out_lines: list[str] = []
    for line in raw.splitlines():
        if any(_index_line_matches_name(line, n) for n in names_to_remove):
            continue
        out_lines.append(line)
    index_path.write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")


class MemoryConsolidator:
    """memdir consolidate 实现；dry_run → 用户确认 → apply。"""

    def __init__(
        self,
        *,
        llm: LLMClient | LLMRouter,
        memdir_loader: MemdirLoader,
        instruction: str,
    ) -> None:
        self._llm = llm
        self._memdir_loader = memdir_loader
        self._instruction = instruction

    async def dry_run(self, session_id: str) -> ConsolidateDiff:
        """喂全部条目 + 当前索引给 LLM，解析 JSON diff，不落盘。"""
        snap = self._memdir_loader.get()
        entries = snap.entries
        if not entries:
            return ConsolidateDiff()

        dump = _dump_entries_for_llm(entries)
        index_text = snap.index_text.strip() or "(空)"
        user_payload = (
            f"## 全部记忆条目 ({len(entries)} 条)\n\n"
            f"```json\n{dump}\n```\n\n"
            f"## 当前 MEMORY.md 索引\n\n```\n{index_text}\n```"
        )
        messages = [
            {"role": "system", "content": self._instruction},
            {"role": "user", "content": user_payload},
        ]

        try:
            result = await self._llm.chat(
                messages=messages,
                session_id=session_id,
                channel="memory-consolidate-internal",
                temperature=0.2,
            )
        except LLMError as exc:
            _logger.warning("consolidate LLM 调用失败", error=str(exc))
            raise

        try:
            data = _parse_json_object(result.text)
        except ValueError as exc:
            _logger.warning("consolidate 响应非合法 JSON", error=str(exc))
            raise LLMError(f"LLM 返回的 consolidate diff 不是合法 JSON：{exc}") from exc

        valid_names = {e.name for e in entries}
        # protected 条目不允许碰：从 valid_names 里排除掉，coerce 时会自动 drop
        protected_names = {e.name for e in entries if e.protected}
        non_protected = valid_names - protected_names

        warnings: list[str] = []
        merge_raw = data.get("merge") or []
        delete_raw = data.get("delete") or []
        rewrite_raw = data.get("rewrite") or []

        merges: list[MergeOp] = []
        for item in merge_raw if isinstance(merge_raw, list) else []:
            op = _coerce_merge(item, non_protected)
            if op is None:
                warnings.append(f"忽略无效 merge 项：{item!r}")
            else:
                merges.append(op)

        deletes: list[DeleteOp] = []
        for item in delete_raw if isinstance(delete_raw, list) else []:
            op_d = _coerce_delete(item, non_protected)
            if op_d is None:
                warnings.append(f"忽略无效 delete 项：{item!r}")
            else:
                deletes.append(op_d)

        rewrites: list[RewriteOp] = []
        for item in rewrite_raw if isinstance(rewrite_raw, list) else []:
            op_r = _coerce_rewrite(item, non_protected)
            if op_r is None:
                warnings.append(f"忽略无效 rewrite 项：{item!r}")
            else:
                rewrites.append(op_r)

        # 处理上限：merge + delete + rewrite 总和 ≤ _MAX_OPS；按原顺序截断
        total = len(merges) + len(deletes) + len(rewrites)
        if total > _MAX_OPS:
            warnings.append(f"变更总数 {total} 超过上限 {_MAX_OPS}，截断到前 {_MAX_OPS} 个")
            # 按原顺序：先 merge，再 delete，再 rewrite，逐个保留直到达到上限
            kept_m: list[MergeOp] = []
            kept_d: list[DeleteOp] = []
            kept_r: list[RewriteOp] = []
            quota = _MAX_OPS
            for m_op in merges:
                if quota == 0:
                    break
                kept_m.append(m_op)
                quota -= 1
            for d_op in deletes:
                if quota == 0:
                    break
                kept_d.append(d_op)
                quota -= 1
            for r_op in rewrites:
                if quota == 0:
                    break
                kept_r.append(r_op)
                quota -= 1
            merges, deletes, rewrites = kept_m, kept_d, kept_r

        return ConsolidateDiff(
            merge=merges, delete=deletes, rewrite=rewrites, warnings=warnings,
        )

    async def apply(self, diff: ConsolidateDiff) -> ConsolidateResult:
        """根据 diff 真正改 memdir 文件 + MEMORY.md；返回每类操作计数 + 错误列表。"""
        result = ConsolidateResult()
        snap = self._memdir_loader.get()
        # name → file_path 反查
        by_name: dict[str, Path] = {e.name: e.file_path for e in snap.entries}
        memdir_root = snap.memdir_root
        index_path = memdir_root / _INDEX_FILE

        # 1) merge：每组独立 try；失败追加到 errors 继续
        names_to_drop_from_index: set[str] = set()
        for op in diff.merge:
            try:
                keep_path = by_name.get(op.keep)
                if keep_path is None or not keep_path.is_file():
                    raise FileNotFoundError(f"keep entry '{op.keep}' 文件不存在")
                # 重写 keep 文件 body
                _rewrite_body_in_file(keep_path, op.new_body)
                # 删除 drop 列表中的每个文件
                for dname in op.drop:
                    dpath = by_name.get(dname)
                    if dpath is None or not dpath.is_file():
                        result.errors.append(f"merge: drop entry '{dname}' 文件不存在，跳过")
                        continue
                    dpath.unlink()
                    names_to_drop_from_index.add(dname)
                result.merged_count += 1
            except Exception as exc:
                result.errors.append(f"merge keep={op.keep}: {type(exc).__name__}: {exc}")

        # 2) delete：unlink 文件 + 标记从索引删
        for d_op in diff.delete:
            try:
                dpath = by_name.get(d_op.name)
                if dpath is None or not dpath.is_file():
                    raise FileNotFoundError(f"delete entry '{d_op.name}' 文件不存在")
                dpath.unlink()
                names_to_drop_from_index.add(d_op.name)
                result.deleted_count += 1
            except Exception as exc:
                result.errors.append(f"delete name={d_op.name}: {type(exc).__name__}: {exc}")

        # 3) rewrite：只动 body；索引不变
        for r_op in diff.rewrite:
            try:
                rpath = by_name.get(r_op.name)
                if rpath is None or not rpath.is_file():
                    raise FileNotFoundError(f"rewrite entry '{r_op.name}' 文件不存在")
                _rewrite_body_in_file(rpath, r_op.new_body)
                result.rewritten_count += 1
            except Exception as exc:
                result.errors.append(f"rewrite name={r_op.name}: {type(exc).__name__}: {exc}")

        # 4) 同步 MEMORY.md：把 merge.drop + delete.name 对应索引行删掉
        try:
            _remove_index_lines(index_path, names_to_drop_from_index)
        except Exception as exc:
            result.errors.append(f"MEMORY.md 索引更新失败：{type(exc).__name__}: {exc}")

        # 5) 让下次 get() 重新扫盘
        self._memdir_loader.invalidate()
        return result


__all__ = [
    "ConsolidateDiff",
    "ConsolidateResult",
    "DeleteOp",
    "MemoryConsolidator",
    "MergeOp",
    "RewriteOp",
    "load_consolidate_instruction",
]
