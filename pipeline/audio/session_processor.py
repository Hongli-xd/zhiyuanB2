"""
会话状态 Processor（pipecat 形态）。

把 SessionState（纯逻辑三态机）接入 pipecat 流水线：消费经过的帧来驱动状态转换，
并管理真正的 asyncio 空闲计时器、按状态切灯带。

放在流水线靠前（紧跟音频源之后），这样它能看到：
  TranscriptionFrame        用户说完一句话被 ASR 转出 → on_transcription → PROCESSING
  BotStoppedSpeakingFrame   机器人 TTS 播完(一轮结束) → on_turn_complete → LISTENING
  StartInterruptionFrame    用户插话打断 → on_interrupt → LISTENING
  UserStartedSpeakingFrame  用户开始说话 → on_speech_activity（重置空闲计时器）

为什么用「TTS 播完」而不是「LLM 文本结束」作为一轮结束信号：
  pipecat 的 BotStoppedSpeakingFrame 表示机器人实际说完了，用它回到 LISTENING
  才不会在用户还没听完时就重置——这正好解决了状态机设计里「一轮何时结束」的待定问题。

唤醒来自 ROS（机器人侧唤醒词），不是 pipecat 帧。ROS 线程通过 notify_wakeup()
跨线程通知本 processor。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from pipecat.frames.frames import (
    Frame, StartFrame, EndFrame,
    TranscriptionFrame, UserStartedSpeakingFrame,
    BotStoppedSpeakingFrame, StartInterruptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from pipeline.audio.session_state import SessionState, State
from pipeline.audio.task_interrupt import TaskInterruptManager, Phase

log = logging.getLogger("a2.session_proc")

# 状态 → 灯带预设
_STATE_LIGHT = {
    State.SLEEPING: "off",
    State.LISTENING: "waiting",
    State.PROCESSING: "working",
}


class SessionStateProcessor(FrameProcessor):
    def __init__(self, idle_timeout: float = 60.0, set_light=None, wakeup_tts: Optional[str] = "我在呢",
                 task_manager: Optional[TaskInterruptManager] = None):
        super().__init__()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._idle_task: Optional[asyncio.Task] = None
        self._wakeup_tts = wakeup_tts
        # set_light: async callable(preset:str) -> None；默认用 capabilities 的灯带工具
        self._set_light = set_light or self._default_set_light

        self._session = SessionState(
            idle_timeout=idle_timeout,
            on_transition=self._on_transition,
            on_arm_timer=self._arm_timer,
            on_cancel_timer=self._cancel_timer,
        )

        # 任务中断管理（正交于会话三态机）。注入默认实现，测试可替换。
        self._task_mgr = task_manager or self._build_default_task_manager(idle_timeout)
        # 区分「这次 TTS 是追问」还是「正常回答」，避免追问后又触发追问
        self._expecting_followup_reply = False

    def _build_default_task_manager(self, idle_timeout: float) -> TaskInterruptManager:
        from capabilities.task.task_control import pause_task, resume_task, stop_task
        from capabilities.task.intent import classify as classify_intent
        import config
        return TaskInterruptManager(
            resume_timeout=getattr(config, "TASK_RESUME_TIMEOUT", 420.0),
            pause_fn=pause_task, resume_fn=resume_task, stop_fn=stop_task,
            say_fn=self._say,
            classify_fn=lambda t: classify_intent(t, llm_call=None),
        )

    @property
    def task_manager(self) -> TaskInterruptManager:
        return self._task_mgr

    @property
    def session(self) -> SessionState:
        return self._session

    # ── 帧处理 ────────────────────────────────────────────────────────────
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            self._loop = asyncio.get_running_loop()

        elif isinstance(frame, UserStartedSpeakingFrame):
            self._session.on_speech_activity()

        elif isinstance(frame, TranscriptionFrame):
            self._session.on_transcription()
            # 若处于「等用户表态是否继续任务」阶段，拦截这句话先做意图路由：
            # 只有 OTHER(回答别的问题) 才把转写继续下推给 LLM；
            # RESUME/ABANDON 由任务管理器处理，不应作为普通提问进 LLM。
            if self._task_mgr.phase == Phase.AWAITING:
                route = await self._task_mgr.on_user_reply(frame.text)
                if route != "answer":
                    return  # 已处理(恢复/放弃)，不下推
                # OTHER：作为普通问题继续，下面正常 push
            await self.push_frame(frame, direction)
            return

        elif isinstance(frame, StartInterruptionFrame):
            self._session.on_interrupt()

        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._session.on_turn_complete()
            # 一轮回答播完：若有挂起任务且这次不是追问本身，则追问是否继续
            if self._task_mgr.has_suspended and not self._expecting_followup_reply:
                self._loop.create_task(self._followup())
            self._expecting_followup_reply = False

        elif isinstance(frame, EndFrame):
            self._cancel_timer()

        await self.push_frame(frame, direction)

    async def _followup(self) -> None:
        """追问『要继续吗』。标记下次 BotStopped 不再触发追问。"""
        self._expecting_followup_reply = True
        await self._task_mgr.on_answer_complete()

    # ── 来自 ROS 线程的唤醒通知（跨线程安全）────────────────────────────────
    def notify_wakeup(self) -> None:
        """ROS 线程收到唤醒词时调用。把状态转换调度回事件循环线程执行。"""
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._handle_wakeup)

    def _handle_wakeup(self) -> None:
        first_time = self._session.state == State.SLEEPING
        self._session.on_wakeup()
        # 仅在从休眠唤醒时播一句问候（连续对话中重复唤醒不再打扰）
        if first_time and self._wakeup_tts:
            self._loop.create_task(self._say(self._wakeup_tts))
        # 唤醒时若有任务正在执行，暂停它并进入「中断对话」模式
        self._loop.create_task(self._maybe_interrupt_task())

    async def _maybe_interrupt_task(self) -> None:
        """检测是否有 RUNNING 任务，有则暂停挂起。"""
        if self._task_mgr.has_suspended:
            return
        try:
            from capabilities.task.task_query import find_running_task, get_task_name
            task_id = await find_running_task()
            if not task_id:
                return
            name = await get_task_name(task_id) or ""
            await self._task_mgr.on_wakeup_interrupt(task_id, name)
        except Exception as e:
            log.error("检测/暂停运行中任务失败: %s", e)

    # ── 空闲计时器（真正的 asyncio 定时器，不依赖音频帧）─────────────────────
    def _arm_timer(self) -> None:
        self._cancel_timer()
        if self._loop and self._loop.is_running():
            self._idle_task = self._loop.create_task(self._idle_countdown())

    def _cancel_timer(self) -> None:
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
        self._idle_task = None

    async def _idle_countdown(self) -> None:
        try:
            await asyncio.sleep(self._session.idle_timeout)
        except asyncio.CancelledError:
            return
        # 到点：在事件循环里触发状态机回 SLEEPING
        self._session.idle_timeout_fired()

    # ── 状态变化副作用：切灯带 ────────────────────────────────────────────
    def _on_transition(self, old: State, new: State) -> None:
        preset = _STATE_LIGHT.get(new)
        if preset and self._loop:
            self._loop.create_task(self._safe_set_light(preset))

    async def _safe_set_light(self, preset: str) -> None:
        try:
            await self._set_light(preset)
        except Exception as e:
            log.error("切灯带 %s 失败: %s", preset, e)

    async def _default_set_light(self, preset: str) -> None:
        from capabilities.display.light import set_status_light
        await set_status_light(preset=preset)

    async def _say(self, text: str) -> None:
        try:
            from services.tts import play_tts
            await play_tts(text, interrupt=True)
        except Exception as e:
            log.error("唤醒 TTS 失败: %s", e)
