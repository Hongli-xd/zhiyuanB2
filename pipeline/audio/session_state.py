"""
会话状态机（纯逻辑，不依赖 pipecat / ROS，可单测）。

三态模型（基于 A2 硬件行为：唤醒后麦克风才发帧，静默时不发帧）：

    SLEEPING ──唤醒──► LISTENING ──ASR出文字──► PROCESSING
       ▲                 │  ▲                       │
       │  空闲计时器到点   │  └──一轮回复播完─────────┘
       └─────────────────┘     (重置计时器)

设计要点：
  - 空闲超时用「独立计时器回调」驱动，不依赖音频帧。
    （A2 没人说话时不发帧，靠帧驱动的超时永远不会触发——这是原代码的 bug 根源。）
    本类只负责「何时该重置/取消/触发」计时器的决策；真正的 asyncio 定时器
    由外层 processor 注入（on_arm_timer / on_cancel_timer 回调），保持本类纯逻辑可测。
  - 一次唤醒后连续多轮对话，60 秒静默才回 SLEEPING（产品决策）。
  - 每次状态变化通过 on_transition 回调通知外层（用于切灯带等副作用）。
"""

from __future__ import annotations

import enum
import logging
from typing import Callable, Optional

log = logging.getLogger("a2.session")


class State(enum.Enum):
    SLEEPING = "sleeping"
    LISTENING = "listening"
    PROCESSING = "processing"


class SessionState:
    """
    回调（由外层 processor 注入，本类只调用、不实现）：
      on_transition(old, new)  状态变化时触发（切灯带等）
      on_arm_timer()           要求外层启动/重置空闲计时器（sleep idle_timeout 后回调 idle_timeout()）
      on_cancel_timer()        要求外层取消空闲计时器
    """

    def __init__(
        self,
        idle_timeout: float = 60.0,
        on_transition: Optional[Callable[[State, State], None]] = None,
        on_arm_timer: Optional[Callable[[], None]] = None,
        on_cancel_timer: Optional[Callable[[], None]] = None,
    ):
        self.idle_timeout = idle_timeout
        self._state = State.SLEEPING
        self._on_transition = on_transition or (lambda o, n: None)
        self._on_arm_timer = on_arm_timer or (lambda: None)
        self._on_cancel_timer = on_cancel_timer or (lambda: None)

    @property
    def state(self) -> State:
        return self._state

    def should_process_audio(self) -> bool:
        """ros_source 问它：当前要不要处理音频帧。仅 LISTENING/PROCESSING 时为真。"""
        return self._state in (State.LISTENING, State.PROCESSING)

    def _transition(self, new: State) -> None:
        if new == self._state:
            return
        old = self._state
        self._state = new
        log.info("会话状态: %s → %s", old.value, new.value)
        self._on_transition(old, new)

    # ── 事件入口（由 processor 根据 pipecat 帧调用）─────────────────────────
    def on_wakeup(self) -> None:
        """收到唤醒。SLEEPING/任意态 → LISTENING，启动空闲计时器。"""
        self._transition(State.LISTENING)
        self._on_arm_timer()

    def on_speech_activity(self) -> None:
        """用户开始说话（UserStartedSpeaking）。仅重置空闲计时器，不改状态。"""
        if self._state == State.LISTENING:
            self._on_arm_timer()  # 重置：有人在说话，别超时

    def on_transcription(self) -> None:
        """ASR 出了文字 → PROCESSING，处理期间取消空闲计时器（长任务不误判空闲）。"""
        if self._state == State.LISTENING:
            self._transition(State.PROCESSING)
            self._on_cancel_timer()

    def on_turn_complete(self) -> None:
        """一轮回复播完（TTS 结束）→ 回 LISTENING，重启空闲计时器（连续多轮）。"""
        if self._state == State.PROCESSING:
            self._transition(State.LISTENING)
            self._on_arm_timer()

    def on_interrupt(self) -> None:
        """用户插话打断 → 回 LISTENING，重启计时器。"""
        if self._state == State.PROCESSING:
            self._transition(State.LISTENING)
            self._on_arm_timer()

    def idle_timeout_fired(self) -> None:
        """空闲计时器到点（外层定时器回调）→ 回 SLEEPING。只在 LISTENING 时生效。"""
        if self._state == State.LISTENING:
            self._on_cancel_timer()
            self._transition(State.SLEEPING)
