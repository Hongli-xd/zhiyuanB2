"""
任务引擎的原子步骤（migrate_to_auto / set_current_task / launch_task）。

这三个是「一次 RPC」的原子操作，本身不直接暴露给 LLM，
而是被 launch_task 这个 skill 编排成「切Auto→设当前→启动」的序列。

判定逻辑沿用原项目（成功响应里含 code:0 / ReturnType_SUCCEED），
那是调试机器人时反复验证过的，原样保留。
"""

from __future__ import annotations

import logging

from config import SYSTEM_SERVICE_BASE, TASK_ENGINE_BASE
from core.http import get_client

log = logging.getLogger("a2.cap.task_engine")


async def migrate_to_auto() -> bool:
    res = await get_client().post(
        f"{SYSTEM_SERVICE_BASE}/MigrateSystemStateSync", {"state": "Auto"}
    )
    ok = res.ok and res.contains('"code":"0"')
    log.info("migrate_to_auto -> %s", ok)
    return ok


async def set_current_task(task_id: str) -> bool:
    res = await get_client().post(
        f"{TASK_ENGINE_BASE}/SetCurrentTask", {"task_id": task_id}
    )
    ok = res.ok and res.contains('"code":"0"')
    log.info("set_current_task(%s) -> %s", task_id, ok)
    return ok


async def launch_task(task_id: str) -> bool:
    res = await get_client().post(
        f"{TASK_ENGINE_BASE}/LaunchTask", {"task_id": task_id}
    )
    ok = res.contains("ReturnType_SUCCEED")
    log.info("launch_task(%s) -> %s", task_id, ok)
    if ok:
        from capabilities.task.task_control import _task_state_callback
        if _task_state_callback:
            _task_state_callback("RUNNING")
    return ok
