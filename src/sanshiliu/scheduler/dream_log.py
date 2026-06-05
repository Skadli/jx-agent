"""做梦历史日志；append-only 落 <data_dir>/dream-log.json，心跳页读最近几次。

为什么独立成文件：做梦不像成长有结构化状态机，它的产物是写进 memdir 的反思条目；但
"哪天做了/跳过没跳过/产出了什么"需要一个可回看的轻量历史——而心跳本身只在内存里存
last-run（且不持久化，重启即丢，见 persistence.py 的 _PERSIST_FIELDS）。这里只做
"追加一条 + 截断 + 原子写"，best-effort：读写失败都只记日志、绝不抛，不能拖垮做梦。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sanshiliu.foundation.logging import get_logger

_logger = get_logger(__name__)

# 最多保留多少条历史（做梦日级低频，50 条足够回看；防文件无限增长）
_KEEP = 50


def _read_all(path: Path) -> list[dict[str, Any]]:
    """读出全部记录（按写入正序）；文件缺失/损坏/非列表都返回空列表。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, dict)]


def append_dream_record(path: Path, record: dict[str, Any], *, keep: int = _KEEP) -> None:
    """把一条做梦记录追加进 dream-log.json（load→append→截断到 keep→原子写）。best-effort，不抛。"""
    try:
        records = _read_all(path)
        records.append(record)
        records = records[-keep:]
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(path)
    except OSError as exc:
        _logger.warning("dream-log 写盘失败（不阻塞做梦）", path=str(path), error=str(exc))


def load_dream_records(path: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    """读 dream-log.json，返回最近 limit 条（**最新在前**）；limit<=0 返回全部（最新在前）。"""
    records = list(reversed(_read_all(path)))
    return records[:limit] if limit > 0 else records
