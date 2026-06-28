"""
VAD 缓冲状态机（纯逻辑，无 ROS / 无 IO，可单测）。

A2 麦克风按 VAD 状态分帧推送：
  1 = 语音开始（清空缓冲，开始累积）
  2 = 语音中（持续累积）
  3 = 语音结束（累积末帧，整段交给上层送 ASR）
  0 = 静默（复位）

把这段从 ROS 处理器里抽出来单独成类，好处：
  - 逻辑纯粹、可单元测试，不依赖机器人环境。
  - ROS 处理器只负责「收消息、解 protobuf、调本状态机」，职责单一。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Optional


class VadState(IntEnum):
    SILENCE = 0
    BEGIN = 1
    SPEAKING = 2
    END = 3


@dataclass
class VadEvent:
    """状态机处理一帧后的输出。"""
    started: bool = False           # 本帧是否标志语音开始
    stopped: bool = False           # 本帧是否标志语音结束
    utterance: Optional[bytes] = None  # 语音结束时给出的完整音频（否则 None）


class VadBuffer:
    """累积一段话的音频，VAD END 时吐出完整 utterance。"""

    MIN_BYTES = 6400  # < ~0.2s 视为过短，丢弃

    def __init__(self):
        self._buf = bytearray()
        self._recording = False

    def feed(self, vad_state: int, audio: bytes) -> VadEvent:
        if vad_state == VadState.BEGIN:
            self._buf.clear()
            self._recording = True
            if audio:
                self._buf.extend(audio)
            return VadEvent(started=True)

        if vad_state == VadState.SPEAKING:
            if self._recording and audio:
                self._buf.extend(audio)
            return VadEvent()

        if vad_state == VadState.END:
            if self._recording and audio:
                self._buf.extend(audio)
            self._recording = False
            total = len(self._buf)
            if total < self.MIN_BYTES:
                self._buf.clear()
                return VadEvent(stopped=True)  # 太短：标记结束但无 utterance
            utterance = bytes(self._buf)
            self._buf.clear()
            return VadEvent(stopped=True, utterance=utterance)

        # SILENCE
        if self._recording:
            self._recording = False
        return VadEvent()
