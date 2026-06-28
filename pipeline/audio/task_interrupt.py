"""
任务中断管理器（纯逻辑 + 注入式副作用，可单测）。

管理「被唤醒打断的任务」的完整生命周期，与会话三态机正交：
  唤醒打断 → 暂停任务 → 对话回答 → 每轮答完追问「要继续吗」
  用户回复三分类驱动：RESUME(恢复) / ABANDON(放弃) / OTHER(回答后再追问)
  「先不用」属 OTHER（保持挂起，绝不放弃）
  持续沉默超过 TASK_RESUME_TIMEOUT(7分钟) → 自动放弃(STOP)

与外界的耦合全部通过注入的异步回调，便于测试：
  pause_fn(task_id)   -> ToolResult   暂停并轮询确认
  resume_fn(task_id)  -> ToolResult   恢复并轮询确认
  stop_fn(task_id)    -> ToolResult   终止
  say_fn(text)        -> None          播报（追问/反馈）
  classify_fn(text)   -> str           意图分类 RESUME/ABANDON/OTHER
"""

from __future__ import annotations

import asyncio
import enum
import logging
from typing import Awaitable, Callable, Optional

from core.task_context import SuspendedTask

log = logging.getLogger("a2.task_interrupt")


class Phase(enum.Enum):
    NONE = "none"            # 无挂起任务
    ANSWERING = "answering"  # 有挂起任务，正在回答用户问题
    AWAITING = "awaiting"    # 已追问「要继续吗」，等用户表态


class TaskInterruptManager:
    def __init__(
        self,
        resume_timeout: float,
        pause_fn: Callable[[str], Awaitable],
        resume_fn: Callable[[str], Awaitable],
        stop_fn: Callable[[str], Awaitable],
        say_fn: Callable[[str], Awaitable],
        classify_fn: Callable[[str], Awaitable],
    ):
        self.resume_timeout = resume_timeout
        self._pause = pause_fn
        self._resume = resume_fn
        self._stop = stop_fn
        self._say = say_fn
        self._classify = classify_fn

        self._suspended: Optional[SuspendedTask] = None
        self._phase = Phase.NONE
        self._timeout_task: Optional[asyncio.Task] = None

    @property
    def has_suspended(self) -> bool:
        return self._suspended is not None

    @property
    def phase(self) -> Phase:
        return self._phase

    # ── 唤醒打断：暂停当前任务 ─────────────────────────────────────────────
    async def on_wakeup_interrupt(self, task_id: str, task_name: str = "") -> bool:
        """唤醒时若有任务在跑，暂停它并记录。返回是否成功挂起。"""
        if self._suspended is not None:
            return True  # 已有挂起任务（重复唤醒）
        result = await self._pause(task_id)
        if not result.ok:
            log.warning("暂停任务 %s 失败: %s", task_id, result.message)
            await self._say("抱歉，我没能暂停当前任务。")
            return False
        self._suspended = SuspendedTask(task_id=task_id, name=task_name)
        self._phase = Phase.ANSWERING
        log.info("任务 %s 已挂起，进入对话", task_id)
        return True

    # ── 一轮回答结束：追问是否继续 ─────────────────────────────────────────
    async def on_answer_complete(self) -> None:
        """
        普通问答的一轮回答播完后调用。若有挂起任务，追问「要继续吗」并进入 AWAITING。
        （追问本身不算一轮普通回答，避免追问套追问——由调用方用 is_followup 区分。）
        """
        if self._suspended is None or self._phase == Phase.AWAITING:
            return
        self._phase = Phase.AWAITING
        await self._say(f"对了，要继续刚才的{self._suspended.display()}吗？")
        self._arm_timeout()

    # ── 用户在 AWAITING 时的回复：三分类路由 ───────────────────────────────
    async def on_user_reply(self, text: str) -> str:
        """
        返回路由结果：
          "resumed"  已恢复任务，清空挂起，正常回到无任务对话
          "abandoned" 已放弃任务，清空挂起
          "answer"   是别的问题/推迟，交回正常问答流程（调用方据此让 LLM 回答）
        仅在有挂起任务时有意义。
        """
        if self._suspended is None:
            return "answer"

        intent = await self._classify(text)
        log.info("挂起追问回复「%s」→ 意图 %s", text, intent)
        self._cancel_timeout()

        if intent == "RESUME":
            await self._do_resume()
            return "resumed"
        if intent == "ABANDON":
            await self._do_abandon(reason="用户放弃")
            return "abandoned"
        # OTHER：别的问题或「先不用」→ 回答它，回 ANSWERING（答完会再次追问）
        self._phase = Phase.ANSWERING
        return "answer"

    # ── 沉默超时：自动放弃 ────────────────────────────────────────────────
    def _arm_timeout(self) -> None:
        self._cancel_timeout()
        loop = asyncio.get_event_loop()
        self._timeout_task = loop.create_task(self._timeout_countdown())

    def _cancel_timeout(self) -> None:
        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()
        self._timeout_task = None

    async def _timeout_countdown(self) -> None:
        try:
            await asyncio.sleep(self.resume_timeout)
        except asyncio.CancelledError:
            return
        log.info("追问后沉默超过 %.0fs，自动放弃任务", self.resume_timeout)
        await self._do_abandon(reason="沉默超时")

    # ── 内部动作 ──────────────────────────────────────────────────────────
    async def _do_resume(self) -> None:
        task = self._suspended
        result = await self._resume(task.task_id)
        if result.ok:
            await self._say(f"好的，继续{task.display()}。")
        else:
            await self._say(f"抱歉，{task.display()}恢复失败了。")
        self._clear()

    async def _do_abandon(self, reason: str) -> None:
        task = self._suspended
        if task is None:
            return
        await self._stop(task.task_id)
        if reason != "沉默超时":
            await self._say(f"好的，已取消{task.display()}。")
        self._clear()

    def _clear(self) -> None:
        self._cancel_timeout()
        self._suspended = None
        self._phase = Phase.NONE
