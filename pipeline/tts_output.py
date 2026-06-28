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
    BotStoppedSpeakingFrame,
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

        # LLM 流式文本：只累积，等完全说完再播，避免频繁打断导致卡顿
        if isinstance(frame, (LLMTextFrame, TextFrame)):
            self._buffer += frame.text
            # 不再按标点 flush，等 LLMFullResponseEndFrame 再说
            await self.push_frame(frame, direction)
            return

        # 一轮回复结束，flush 整句
        if isinstance(frame, LLMFullResponseEndFrame):
            log.info("🔊 LLMFullResponseEndFrame 收到, buffer='%s'", self._buffer[:30])
            await self._flush()

        await self.push_frame(frame, direction)

    async def _flush(self):
        text = self._buffer.strip()
        self._buffer = ""
        if not text:
            return
        log.info("🔊 TTS flush: 播放「%s」", text[:50])
        await play_tts(text, interrupt=True)
        await self.push_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        log.info("🔊 TTS flush: BotStoppedSpeakingFrame 已发送")
