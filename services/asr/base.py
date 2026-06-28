"""ASR 抽象基类：输入 16k/16bit/单声道 PCM，输出 (text, confidence)。"""

from __future__ import annotations

from typing import Optional, Tuple


class BaseASR:
    async def transcribe(self, pcm: bytes) -> Tuple[str, Optional[float]]:
        raise NotImplementedError
