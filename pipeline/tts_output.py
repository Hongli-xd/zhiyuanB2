"""
A2 TTS 输出处理器。

把 Pipecat 下行的文本（LLM 生成的回复）送到 A2 的 TTSService/PlayTTS 播报。
同时处理打断：收到 InterruptionFrame（用户插话）时调 StopTTS 立刻闭嘴。

放在 pipeline 末端，替代 Pipecat 自带的云 TTS service —— 因为 A2 自己负责发声。
"""

import logging

from pipecat.frames.frames import (
    Frame,
    TextFrame,
    LLMTextFrame,
    TTSSpeakFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from services.tts import play_tts, stop_all_tts

log = logging.getLogger("a2.tts_out")


class A2TTSProcessor(FrameProcessor):
    """累积 LLM 文本帧，整句送 A2 PlayTTS。"""

    def __init__(self):
        super().__init__()
        self._buffer = ""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        # 用户插话 -> 立刻停止当前播报
        if isinstance(frame, InterruptionFrame):
            self._buffer = ""
            await stop_all_tts()
            await self.push_frame(frame, direction)
            return

        # 显式要播报的整句
        if isinstance(frame, TTSSpeakFrame):
            await play_tts(frame.text, interrupt=True)
            await self.push_frame(frame, direction)
            return

        # LLM 流式文本：累积，遇到句末标点就成句播报（降低首字延迟）
        if isinstance(frame, (LLMTextFrame, TextFrame)):
            self._buffer += frame.text
            if any(p in self._buffer for p in "。！？!?\n"):
                await self._flush()
            await self.push_frame(frame, direction)
            return

        # 一轮回复结束，flush 残余
        if isinstance(frame, LLMFullResponseEndFrame):
            await self._flush()

        await self.push_frame(frame, direction)

    async def _flush(self):
        text = self._buffer.strip()
        self._buffer = ""
        if not text:
            return

        # motion 由 LLM 主动调用 play_motion 工具触发，这里不再自动触发（避免重复）
        await play_tts(text, interrupt=False)
