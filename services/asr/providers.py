"""
ASR 实现集（可插拔）。

  openai  —— OpenAI 兼容 /audio/transcriptions
  whisper —— 本地 faster-whisper（断网可用）
  funasr  —— 阿里云 dashscope 实时识别

输入 16kHz/16bit/单声道 PCM，输出 (text, confidence)。
build_asr() 按 config.ASR_PROVIDER 选实现。
"""

from __future__ import annotations

import io
import logging
import wave
from typing import Optional, Tuple

import config
from services.asr.base import BaseASR

log = logging.getLogger("a2.asr")


def _pcm_to_wav(pcm: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


class OpenAICompatibleASR(BaseASR):
    def __init__(self):
        self.base_url = config.ASR_BASE_URL.rstrip("/")
        self.api_key = config.ASR_API_KEY
        self.model = config.ASR_MODEL
        self.language = config.ASR_LANGUAGE

    async def transcribe(self, pcm: bytes) -> Tuple[str, Optional[float]]:
        import aiohttp

        wav = _pcm_to_wav(pcm, config.AUDIO_SAMPLE_RATE, config.AUDIO_CHANNELS)
        url = f"{self.base_url}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        form = aiohttp.FormData()
        form.add_field("file", wav, filename="audio.wav", content_type="audio/wav")
        form.add_field("model", self.model)
        form.add_field("language", self.language)
        form.add_field("response_format", "json")
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, data=form, headers=headers, timeout=15) as r:
                    data = await r.json(content_type=None)
                    text = (data or {}).get("text", "").strip()
                    log.info("ASR(cloud) -> 「%s」", text)
                    return text, None
        except Exception as e:
            log.error("云 ASR 失败: %s", e)
            return "", None


class FasterWhisperASR(BaseASR):
    def __init__(self):
        self._model = None

    def _ensure(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            log.info("加载 faster-whisper: %s on %s", config.WHISPER_MODEL_SIZE, config.WHISPER_DEVICE)
            self._model = WhisperModel(
                config.WHISPER_MODEL_SIZE, device=config.WHISPER_DEVICE,
                compute_type=config.WHISPER_COMPUTE,
            )

    async def transcribe(self, pcm: bytes) -> Tuple[str, Optional[float]]:
        import asyncio, math
        import numpy as np
        self._ensure()
        audio = np.frombuffer(pcm, dtype=np.int16).astype("float32") / 32768.0

        def _run():
            segments, _info = self._model.transcribe(audio, language=config.ASR_LANGUAGE, beam_size=5)
            segs = list(segments)
            text = "".join(s.text for s in segs).strip()
            conf = None
            if segs:
                avg = sum(s.avg_logprob for s in segs) / len(segs)
                conf = max(0.0, min(1.0, math.exp(avg)))
            return text, conf

        text, conf = await asyncio.get_event_loop().run_in_executor(None, _run)
        log.info("ASR(local) -> 「%s」 conf=%s", text, conf)
        return text, conf


class FunASR(BaseASR):
    """
    阿里云 dashscope 识别。直接传 PCM bytes，不落临时文件（省一次磁盘 IO）。
    在线程池里跑同步 SDK，避免阻塞事件循环。
    """

    def __init__(self):
        import dashscope
        dashscope.api_key = config.ASR_API_KEY
        self.model = config.FUNASR_MODEL
        self.lang_hints = config.FUNASR_LANGUAGE_HINTS.split(",")

    async def transcribe(self, pcm: bytes) -> Tuple[str, Optional[float]]:
        import asyncio
        wav = _pcm_to_wav(pcm, config.AUDIO_SAMPLE_RATE, 1)

        def _run():
            from dashscope.audio.asr import Recognition
            rec = Recognition(
                model=self.model, format="wav", sample_rate=config.AUDIO_SAMPLE_RATE,
                language_hints=self.lang_hints, callback=None,
            )
            # dashscope 支持传入 bytes 流，免去临时文件
            result = rec.call(io.BytesIO(wav))
            if result.status_code == 200:
                sentences = result.get_sentence() or []
                if sentences:
                    return sentences[0].get("text", "").strip(), None
            return "", None

        try:
            text, conf = await asyncio.get_event_loop().run_in_executor(None, _run)
            log.info("ASR(funasr) -> 「%s」", text)
            return text, conf
        except Exception as e:
            log.error("FunASR 识别异常: %s", e)
            return "", None


def build_asr() -> BaseASR:
    provider = config.ASR_PROVIDER
    if provider == "whisper":
        return FasterWhisperASR()
    if provider == "funasr":
        return FunASR()
    return OpenAICompatibleASR()
