"""
A2 TTS 播放服务封装（文档 7.5）。

play_tts() 调 TTSService/PlayTTS 播报；stop_all_tts() 调 StopTTS 清队列（打断时用）。
统一走 core.http 客户端。
"""

from __future__ import annotations

import logging
import uuid

from config import TTS_BASE, TTS_PRIORITY, TTS_DOMAIN
from core.http import get_client

log = logging.getLogger("a2.tts")


async def play_tts(text: str, interrupt: bool = True) -> dict:
    """在 A2 上播报一段文本。interrupt=True 打断同优先级正在播的内容。"""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "reason": "空文本"}

    trace_id = f"agent_{uuid.uuid4().hex[:12]}"
    payload = {
        "text": text[:1024],
        "priority_level": TTS_PRIORITY,
        "domain": TTS_DOMAIN,
        "trace_id": trace_id,
        "is_interrupted": interrupt,
    }
    res = await get_client().post(f"{TTS_BASE}/PlayTTS", payload)
    log.info("🔊 TTS 播放 -> 「%s」 trace_id=%s", text, trace_id)
    return {"ok": res.ok, "trace_id": trace_id}


async def stop_all_tts() -> dict:
    """终止当前及队列中所有 TTS（用户打断时调用）。"""
    res = await get_client().post(f"{TTS_BASE}/StopTTS", {})
    log.info("🔇 TTS 停止")
    return {"ok": res.ok}
