"""
被挂起任务的进程内状态容器。

任务的物理进度由 A2 任务引擎在机器人侧保存（PAUSE 冻结），所以这里只需记住
「我暂停了哪个任务」这件事，不需要持久化、不需要存进度。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SuspendedTask:
    task_id: str
    name: str = ""           # 任务名，用于追问时说人话（"接人任务"）

    def display(self) -> str:
        return self.name or f"任务{self.task_id}"
