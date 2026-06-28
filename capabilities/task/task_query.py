"""
任务状态查询（GetTask / GetAllTasks）。

提供任务的「真值来源」：不靠猜，直接查 A2 任务引擎当前状态。
状态枚举（文档 7.9.2）：
  StateType_IDLE / RUNNING / PAUSED / STOPPED
  StateType_PAUSING / RESUMING / STOPPING        （异步中间态）
  StateType_FAILPAUSE / FAILRESUME / FAILSTOP     （失败态）
  StateType_EXCEPTION / UNDEFINED / LAZY_PAUSING
"""

from __future__ import annotations

import logging
from typing import Optional

from config import TASK_ENGINE_BASE
from core.http import get_client

log = logging.getLogger("a2.cap.task_query")

# 终态分类，供轮询判断
PAUSED_OK = "StateType_PAUSED"
RUNNING_OK = "StateType_RUNNING"
STOPPED_OK = "StateType_STOPPED"
FAIL_STATES = {
    "StateType_FAILPAUSE", "StateType_FAILRESUME",
    "StateType_FAILSTOP", "StateType_EXCEPTION",
}


async def get_task_state(task_id: str) -> Optional[str]:
    """查单个任务的当前 state 字符串；查不到返回 None。"""
    res = await get_client().post(f"{TASK_ENGINE_BASE}/GetTask", {"task_id": task_id})
    if not res.ok or not res.json:
        return None
    data = res.json.get("data")
    # GetTask 返回单个任务对象（非数组），GetAllTasks 返回数组
    if isinstance(data, dict):
        return data.get("state") if str(data.get("task_id")) == str(task_id) else None
    for t in (data or []):
        if str(t.get("task_id")) == str(task_id):
            return t.get("state")
    return None


async def get_task_name(task_id: str) -> Optional[str]:
    """查任务名称，用于追问时说人话。"""
    res = await get_client().post(f"{TASK_ENGINE_BASE}/GetTask", {"task_id": task_id})
    if not res.ok or not res.json:
        return None
    data = res.json.get("data")
    # GetTask 返回单个任务对象（非数组）
    if isinstance(data, dict):
        return data.get("name") if str(data.get("task_id")) == str(task_id) else None
    for t in (data or []):
        if str(t.get("task_id")) == str(task_id):
            return t.get("name")
    return None


async def find_running_task() -> Optional[str]:
    """从全量任务中找出当前 RUNNING 的 task_id（唤醒打断时用）。"""
    res = await get_client().post(f"{TASK_ENGINE_BASE}/GetAllTasks", {})
    log.info("[find_running_task] GetAllTasks ok=%s json=%s", res.ok, res.json)
    if not res.ok or not res.json:
        return None
    for t in (res.json.get("data") or []):
        if t.get("state") == RUNNING_OK:
            tid = str(t.get("task_id"))
            log.info("[find_running_task] 找到 RUNNING 任务: %s", tid)
            return tid
    log.info("[find_running_task] 没有 RUNNING 任务")
    return None
