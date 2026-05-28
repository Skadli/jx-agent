"""Heartbeat 状态持久化；写 <data_dir>/heartbeat.json，重启 reload。

为什么不塞 settings.json：那个是 Claude 权限协议特定格式（permission rules），混入心跳
配置会污染协议。心跳是 jx-agent 独有功能，独立文件最干净。

文件格式：

```json
{
  "tasks": {
    "dream": {
      "enabled": true,
      "daily_at_hour": 3,
      "interval_seconds": null,
      "extra_params": {"min_sessions": 3}
    }
  }
}
```

读：启动时 register task（env 当 seed）→ load JSON → apply 到 task 字段（缺字段保留）
写：set_enabled / update_config 触发 → dump 全部 task 当前状态
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sanshiliu.foundation.logging import get_logger
from sanshiliu.scheduler.heartbeat import HeartbeatScheduler, HeartbeatTask

_logger = get_logger(__name__)

# 落盘字段白名单——只持久化用户可改的，运行状态（last_run_at 等）不持久化
_PERSIST_FIELDS = ("enabled", "daily_at_hour", "interval_seconds", "extra_params")


def heartbeat_state_path(data_dir: Path) -> Path:
    return data_dir / "heartbeat.json"


def load_heartbeat_state(path: Path) -> dict[str, dict[str, Any]]:
    """加载持久化状态；文件不存在/坏 JSON 返空 dict，不抛。

    返回结构：{task_name: {enabled, daily_at_hour, interval_seconds, extra_params}}
    """
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("heartbeat.json 解析失败，按空配置启动", path=str(path), error=str(exc))
        return {}
    tasks = raw.get("tasks") if isinstance(raw, dict) else None
    if not isinstance(tasks, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for name, cfg in tasks.items():
        if isinstance(name, str) and isinstance(cfg, dict):
            out[name] = cfg
    return out


def apply_state_to_scheduler(
    scheduler: HeartbeatScheduler,
    state: dict[str, dict[str, Any]],
) -> None:
    """把 load 出来的 state 应用到 scheduler 里**已注册**的 task。

    缺字段保留原值（env seed 留住）；多余字段忽略。状态文件里有 scheduler 没注册的
    task name 也忽略（保留以备未来注册）。
    """
    for name, cfg in state.items():
        task = scheduler.get(name)
        if task is None:
            _logger.debug("heartbeat.json 有未注册 task，跳过", name=name)
            continue
        if "enabled" in cfg and isinstance(cfg["enabled"], bool):
            task.enabled = cfg["enabled"]
        if "daily_at_hour" in cfg:
            v = cfg["daily_at_hour"]
            if v is None or (isinstance(v, int) and 0 <= v <= 23):
                task.daily_at_hour = v
        if "interval_seconds" in cfg:
            v = cfg["interval_seconds"]
            if v is None or (isinstance(v, int) and v >= 1):
                task.interval_seconds = v
        if "extra_params" in cfg and isinstance(cfg["extra_params"], dict):
            # 只采纳在 editable_params 白名单内的键
            for k, val in cfg["extra_params"].items():
                if k in task.editable_params:
                    task.extra_params[k] = val
        _logger.info("heartbeat task 状态已 reload", name=name, enabled=task.enabled)


def save_heartbeat_state(path: Path, scheduler: HeartbeatScheduler) -> None:
    """把 scheduler 当前所有 task 的可持久化字段 dump 到 JSON。

    原子写：先写 .tmp 再 rename，避免半写文件破坏下次启动。
    """
    payload: dict[str, Any] = {"tasks": {}}
    for task in scheduler.list_tasks():
        payload["tasks"][task.name] = _task_persist_fields(task)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        _logger.error("heartbeat.json 写盘失败", path=str(path), error=str(exc))


def _task_persist_fields(task: HeartbeatTask) -> dict[str, Any]:
    return {f: getattr(task, f) for f in _PERSIST_FIELDS}
