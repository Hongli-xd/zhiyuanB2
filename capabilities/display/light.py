"""
能力：灯带状态指示（set_status_light）。

向现场人员指示机器人当前状态。属于无害动作，标注 CONCURRENT，
可与其它并发工具同时执行（例如「亮灯 + 闲聊」无需互相等待）。

对应原 curl：
  POST .../HalRgbLightService/SetRgbLightCommand
  {"cmd": {"red":180,"green":0,"blue":100,"effect":2,"control":1}}
"""

from __future__ import annotations

import logging
from typing import Optional

from config import LIGHT_BASE, LIGHT_PRESETS
from core.capability import Concurrency, tool
from core.http import get_client
from core.result import ToolResult

log = logging.getLogger("a2.cap.light")


@tool(
    name="set_status_light",
    description=(
        "设置A2机器人灯带颜色/效果，用于向现场人员指示当前状态。"
        "预设：waiting=等待(紫红), working=工作(蓝), done=完成(绿), off=关闭。"
        "也支持自定义RGB(0-255)。"
    ),
    properties={
        "preset": {"type": "string", "enum": ["waiting", "working", "done", "off"]},
        "red": {"type": "integer", "minimum": 0, "maximum": 255},
        "green": {"type": "integer", "minimum": 0, "maximum": 255},
        "blue": {"type": "integer", "minimum": 0, "maximum": 255},
    },
    required=[],
    concurrency=Concurrency.CONCURRENT,
)
async def set_status_light(
    preset: Optional[str] = None,
    red: Optional[int] = None,
    green: Optional[int] = None,
    blue: Optional[int] = None,
    effect: int = 2,
    control: int = 1,
) -> ToolResult:
    # 取基准：优先 preset，否则用裸 RGB
    if preset and preset in LIGHT_PRESETS:
        cmd = dict(LIGHT_PRESETS[preset])
    else:
        cmd = {"red": 0, "green": 0, "blue": 0, "effect": effect, "control": control}

    # RGB 显式覆盖
    if red is not None:
        cmd["red"] = max(0, min(255, red))
    if green is not None:
        cmd["green"] = max(0, min(255, green))
    if blue is not None:
        cmd["blue"] = max(0, min(255, blue))
    if preset is None:
        cmd["effect"] = effect
        cmd["control"] = control

    res = await get_client().post(f"{LIGHT_BASE}/SetRgbLightCommand", {"cmd": cmd})
    if res.ok:
        return ToolResult.success("灯带已设置", applied=cmd)
    return ToolResult.fail(f"灯带设置失败({res.status})", applied=cmd)
