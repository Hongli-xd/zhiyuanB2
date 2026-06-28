"""
能力：预设动作播放（play_motion）。

播放机器人事先录制好的预设动作（挥手 / 点头 / 鞠躬等，共133种）。
属于物理动作，标注 EXCLUSIVE 独占。

动作映射表（133条）放在 _motion_data.py，与逻辑分离——数据再多也不会让
本文件臃肿。原项目用 curl 子进程发送，现统一改用 core.http。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import List, Optional, Tuple

from config import A2_LIGHT_HOST
from core.capability import Concurrency, tool
from core.http import get_client
from core.result import ToolResult
from capabilities.motion._motion_data import MOTION_MAP, MOTION_PREFIX

log = logging.getLogger("a2.cap.motion_preset")

MOTION_RPC = f"http://{A2_LIGHT_HOST}:56444/rpc/aimdk.protocol.MotionCommandService/SendMotionCommand"
MOTION_STATUS_RPC = f"http://{A2_LIGHT_HOST}:56444/rpc/aimdk.protocol.MotionCommandService/GetMotionStatus"

# 正在执行的动作集合（去重，防同一动作重复触发）
_running: set[str] = set()

# 时长后缀（秒）→ 毫秒
_DURATION_SUFFIXES = {
    "_8s": 8000,
    "_11s": 11000,
    "_22s": 22000,
    "_25s": 25000,
    "_29s": 29000,
    "_7s": 7000,
    "_12s": 12000,
    "_长": 15000,   # "左手握手_长" 等
}


def _parse_duration_ms(motion_name: str) -> int:
    """从动作名后缀解析时长（毫秒），找不到返回默认 10000ms。"""
    for suffix, ms in _DURATION_SUFFIXES.items():
        if motion_name.endswith(suffix):
            return ms
    return 10000


def _fuzzy_match(name: str, candidates: List[str], top_n: int = 3) -> List[Tuple[str, float]]:
    """字符重叠率 + 包含/子串加权的简单模糊匹配，返回 top_n。"""
    name_clean = re.sub(r"[\s\-_]", "", name)
    scores = []
    for c in candidates:
        c_clean = re.sub(r"[\s\-_]", "", c)
        overlap = len(set(name_clean) & set(c_clean))
        score = overlap / max(len(name_clean), len(c_clean), 1)
        if name_clean in c_clean or c_clean in name_clean:
            score += 0.3
        for i in range(len(name_clean)):
            for j in range(i + 1, len(name_clean) + 1):
                if name_clean[i:j] in c_clean:
                    score += 0.1 * (j - i) / max(len(name_clean), 1)
        scores.append((c, min(score, 1.0)))
    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:top_n]


@tool(
    name="play_motion",
    description=(
        "播放机器人预设动作。可用动作包括：挥手、点头、鞠躬、敬礼、挥手再见、举手、摇摆、踢腿、蹲下、拥抱、摸头、拍桌子、转圈、伸展等共133种。"
        "动作名精确匹配可用列表，不在列表中的动作会返回最接近的建议。"
    ),
    properties={
        "name": {"type": "string", "description": "动作名称（如：敬礼、点头、鞠躬、挥手、伸手、举手、转圈等）。"},
        "duration_ms": {"type": "integer", "description": "持续时间(毫秒)，默认10000。", "default": 10000},
    },
    required=["name"],
    concurrency=Concurrency.EXCLUSIVE,
)
async def play_motion(name: str, duration_ms: int = 10000) -> ToolResult:
    # 去重
    if name in _running:
        return ToolResult.success(f"动作「{name}」执行中，跳过重复")

    # 未知动作：模糊给建议
    if name not in MOTION_MAP:
        matches = _fuzzy_match(name, list(MOTION_MAP.keys()))
        if matches and matches[0][1] > 0.2:
            best = matches[0][0]
            return ToolResult.fail(f"没有「{name}」这个动作，最接近的是「{best}」。", suggest=best)
        sample = ", ".join(sorted(MOTION_MAP.keys())[:10])
        return ToolResult.fail(f"未知动作「{name}」，可用示例: {sample}...")

    # 从动作名后缀解析实际时长（覆盖 user指定的duration_ms）
    actual_ms = _parse_duration_ms(name)
    if actual_ms != 10000:
        duration_ms = actual_ms
        log.info("[motion] 从动作名解析到时长 %dms", duration_ms)

    _running.add(name)
    try:
        payload = {
            "motion_id": MOTION_PREFIX + MOTION_MAP[name],
            "duration_ms": duration_ms,
            "cmd_end": False,   # 不自动复位，等轮询确认完成后再处理
            "cmd_pause": False,
            "cmd_reset": False,
        }
        log.info("[motion] ▶️ 执行动作「%s」(duration_ms=%d)", name, duration_ms)
        res = await get_client().post(MOTION_RPC, payload)
        if not res.ok:
            return ToolResult.fail(f"动作「{name}」执行失败({res.status})")

        # 轮询 GetMotionStatus 等动作完成，避免 TTS 和动作重叠
        elapsed = 0
        poll_interval = 0.2  # 200ms 查一次
        while elapsed < duration_ms + 2000:  # 多等2s容错
            await asyncio.sleep(poll_interval)
            st = await get_client().post(MOTION_STATUS_RPC, {})
            if st.ok and st.json:
                status = st.json.get("status", "")
                log.debug("[motion] 状态轮询: status=%s", status)
                if status == "MotionCommandStatus_IDLE":
                    break
            elapsed += int(poll_interval * 1000)

        return ToolResult.success(f"{name} 已结束")
    finally:
        _running.discard(name)
