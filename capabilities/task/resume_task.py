"""
技能：恢复被暂停的 AimMaster 任务（@skill 类形式）。

恢复任务流程：
    CtrlTaskState(Type_RESUME) → 轮询等待 RUNNING

如果文档不支持 Type_RESUME，这个技能会失败，但不会影响其他功能。
"""

from __future__ import annotations

import logging
from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph

from core.capability import Concurrency, skill
from core.result import ToolResult
from capabilities.task.task_control import resume_task

log = logging.getLogger("a2.skill.resume_task")


class ResumeState(TypedDict, total=False):
    task_id: str
    step: str
    ok: bool
    message: str


async def _node_resume(state: ResumeState) -> ResumeState:
    state["step"] = "resume_task"
    result = await resume_task(state["task_id"])
    if result.ok:
        return {**state, "ok": True, "message": f"任务已恢复，继续执行。"}
    return {**state, "ok": False, "message": result.message or "恢复任务失败。"}


def _gate(state: ResumeState) -> str:
    return "continue" if state.get("ok") else "abort"


def _build_graph():
    g = StateGraph(ResumeState)
    g.add_node("resume", _node_resume)
    g.set_entry_point("resume")
    g.add_conditional_edges("resume", _gate, {"continue": END, "abort": END})
    return g.compile()


@skill(
    name="resume_aimmaster_task",
    description=(
        "恢复一个被暂停的任务，使它继续执行。"
        "仅用于任务之前被唤醒打断并暂停的情况。"
    ),
    properties={
        "task_id": {
            "type": "string",
            "description": "要恢复的任务ID",
        },
    },
    required=["task_id"],
    concurrency=Concurrency.EXCLUSIVE,
)
class ResumeTaskSkill:
    """编译一次 StateGraph，复用。"""

    def __init__(self):
        self._graph = _build_graph()

    async def run(self, task_id: Optional[str] = None, **_ignore) -> ToolResult:
        if not task_id:
            return ToolResult.fail("没有传入 task_id，无法恢复任务。")
        log.info("▶ 执行 resume_task_skill, task_id=%s", task_id)
        final: ResumeState = await self._graph.ainvoke({"task_id": task_id, "ok": False})
        ok = bool(final.get("ok"))
        return ToolResult(ok=ok, message=final.get("message", ""), data={"task_id": task_id})