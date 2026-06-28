"""
技能：启动 AimMaster 任务（@skill 类形式）。

这是一个「带状态、多步编排」的复杂能力，所以用类（@skill）而非函数表达。
内部用 LangGraph StateGraph 把三步串成「失败即中止」的状态机：
    切 Auto 模式  →  设置当前任务  →  启动任务

为什么保留 LangGraph：你提到后续会加入很多 task，未来的任务编排可能出现
分支 / 循环 / 人在环确认等复杂控制流，StateGraph 能平滑承载。当前虽是线性，
但接口对 LLM 只暴露一个工具 launch_aimmaster_task，内部怎么编排可自由演化。

并发策略 EXCLUSIVE：启动任务会触发机器人导航 / 运动等物理动作，
绝不能与其它动作同时跑，由 dispatcher 的全局独占锁保证。
"""

from __future__ import annotations

import logging
from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph

from config import TASK_NAMES
from core.capability import Concurrency, skill
from core.result import ToolResult
from capabilities.task.task_engine import launch_task, migrate_to_auto, set_current_task
from capabilities.display.light import set_status_light

log = logging.getLogger("a2.skill.launch_task")


class LaunchState(TypedDict, total=False):
    task_id: str
    step: str
    ok: bool
    message: str


async def _node_auto(state: LaunchState) -> LaunchState:
    state["step"] = "migrate_to_auto"
    if await migrate_to_auto():
        return {**state, "ok": True}
    return {**state, "ok": False, "message": "切换 Auto 模式失败，已放弃启动任务。"}


async def _node_set_current(state: LaunchState) -> LaunchState:
    state["step"] = "set_current_task"
    if await set_current_task(state["task_id"]):
        return {**state, "ok": True}
    return {**state, "ok": False, "message": "设置当前任务失败，已放弃启动任务。"}


async def _node_launch(state: LaunchState) -> LaunchState:
    state["step"] = "launch_task"
    if await launch_task(state["task_id"]):
        name = TASK_NAMES.get(state["task_id"], state["task_id"])
        await set_status_light(preset="working")  # 顺手给现场可视反馈
        return {**state, "ok": True, "message": f"{name} 已成功启动。"}
    return {**state, "ok": False, "message": "任务启动失败，请确认系统已切换到 Auto 模式。"}


def _gate(state: LaunchState) -> str:
    return "continue" if state.get("ok") else "abort"


def _build_graph():
    g = StateGraph(LaunchState)
    g.add_node("auto", _node_auto)
    g.add_node("set_current", _node_set_current)
    g.add_node("launch", _node_launch)
    g.set_entry_point("auto")
    g.add_conditional_edges("auto", _gate, {"continue": "set_current", "abort": END})
    g.add_conditional_edges("set_current", _gate, {"continue": "launch", "abort": END})
    g.add_edge("launch", END)
    return g.compile()


@skill(
    name="launch_aimmaster_task",
    description=(
        "启动一个在 AimMaster 上预先创建好的任务。"
        "内部自动完成：切换Auto模式→设置当前任务→启动任务三步。"
    ),
    properties={
        "task_id": {
            "type": "string",
            "enum": list(TASK_NAMES.keys()),
            "description": f"任务ID。{TASK_NAMES}",
        },
        "confidence": {
            "type": "number",
            "description": "本次指令识别置信度0-1，可选；低于阈值会被安全门拦截",
        },
    },
    required=["task_id"],
    concurrency=Concurrency.EXCLUSIVE,
)
class LaunchTaskSkill:
    """编译一次 StateGraph，复用。"""

    def __init__(self):
        self._graph = _build_graph()

    async def run(self, task_id: Optional[str] = None, **_ignore) -> ToolResult:
        if not task_id:
            return ToolResult.fail("没有传入 task_id，无法启动任务。")
        log.info("▶ 执行 launch_task_skill, task_id=%s", task_id)
        final: LaunchState = await self._graph.ainvoke({"task_id": task_id, "ok": False})
        ok = bool(final.get("ok"))
        return ToolResult(ok=ok, message=final.get("message", ""), data={"task_id": task_id})
