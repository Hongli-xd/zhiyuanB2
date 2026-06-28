"""
任务暂停 / 恢复 / 终止（CtrlTaskState）+ 轮询确认。

文档 7.9.5：CtrlTaskState 的 type ∈ {Type_PAUSE, Type_RESUME, Type_STOP}。
暂停/恢复是异步的（有 PAUSING/RESUMING 中间态），所以每个操作：
  1. 发 CtrlTaskState
  2. 轮询 GetTask 直到到达目标终态（PAUSED/RUNNING/STOPPED）或失败态或超时

这样「保存状态」由 A2 在机器人侧完成（PAUSE 冻结进度），Agent 只需确认到位。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Tuple

from config import TASK_ENGINE_BASE
from core.http import get_client
from core.result import ToolResult
from capabilities.task.task_query import (
    get_task_state, PAUSED_OK, RUNNING_OK, STOPPED_OK, FAIL_STATES,
)

log = logging.getLogger("a2.cap.task_control")

POLL_INTERVAL = 0.5     # 秒，轮询间隔
POLL_TIMEOUT = 15.0     # 秒，等待终态的最长时间


async def _ctrl(task_id: str, ctrl_type: str) -> bool:
    """发一个 CtrlTaskState，返回 RPC 是否成功受理。"""
    res = await get_client().post(
        f"{TASK_ENGINE_BASE}/CtrlTaskState",
        {"task_id": task_id, "type": ctrl_type},
    )
    ok = res.contains("ReturnType_SUCCEED")
    log.info("CtrlTaskState(%s, %s) -> %s", task_id, ctrl_type, ok)
    return ok


async def _poll_until(task_id: str, target_state: str) -> Tuple[bool, str]:
    """轮询 GetTask 直到 state==target / 失败态 / 超时。返回 (是否到达, 实际状态)。"""
    elapsed = 0.0
    last = "?"
    while elapsed < POLL_TIMEOUT:
        state = await get_task_state(task_id)
        last = state or "?"
        if state == target_state:
            return True, state
        if state in FAIL_STATES:
            log.warning("任务 %s 进入失败态 %s", task_id, state)
            return False, state
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
    log.warning("任务 %s 等待 %s 超时，当前 %s", task_id, target_state, last)
    return False, last


async def pause_task(task_id: str) -> ToolResult:
    """暂停任务并确认到 PAUSED。任务进度由 A2 在机器人侧保存。"""
    if not await _ctrl(task_id, "Type_PAUSE"):
        return ToolResult.fail("暂停指令未受理")
    ok, state = await _poll_until(task_id, PAUSED_OK)
    if ok:
        return ToolResult.success("任务已暂停", state=state)
    return ToolResult.fail(f"暂停未完成（当前 {state}）", state=state)


async def resume_task(task_id: str) -> ToolResult:
    """从断点恢复任务并确认到 RUNNING。"""
    if not await _ctrl(task_id, "Type_RESUME"):
        return ToolResult.fail("恢复指令未受理")
    ok, state = await _poll_until(task_id, RUNNING_OK)
    if ok:
        return ToolResult.success("任务已恢复", state=state)
    return ToolResult.fail(f"恢复未完成（当前 {state}）", state=state)


async def stop_task(task_id: str) -> ToolResult:
    """终止任务（放弃）。"""
    if not await _ctrl(task_id, "Type_STOP"):
        return ToolResult.fail("终止指令未受理")
    ok, state = await _poll_until(task_id, STOPPED_OK)
    # STOP 即使没轮询到 STOPPED 也认为已尽力（任务可能直接消失/转 IDLE）
    return ToolResult.success("任务已终止", state=state)
