"""HeartbeatTask 工厂集合；每个文件一个 build_<name>_task() 给 wire 用。"""

from sanshiliu.scheduler.tasks.dream import build_dream_task

__all__ = ["build_dream_task"]
