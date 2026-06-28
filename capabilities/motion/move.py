"""
能力：运动控制（move）。

控制机器人移动方向和距离。属于危险物理动作，标注 EXCLUSIVE：
执行期间由 dispatcher 的全局独占锁保证不与任何其它工具同时跑。

原项目用 curl 子进程绕开 URL 编码问题；现统一改用 core.http（yarl encoded=True），
全程异步、不阻塞事件循环。payload 结构（header/control_source/data 字段名）
与原项目调通的格式完全一致——那是踩了多个坑才对齐的，原样保留。

安全限制（官方 spec）：
  forward_velocity 推荐 -0.8~0.8；angular_velocity 推荐 -1.0~1.0。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from config import MOTION_BASE
from core.capability import Concurrency, tool
from core.http import get_client
from core.result import ToolResult

log = logging.getLogger("a2.cap.motion")

STEP_LENGTH = 0.3  # m/step，用于「走N步」换算时长

# 角度转弧度
_DEG45 = 0.785398   # 45°
_DEG90 = 1.570796   # 90°
_MAX_ANGULAR = 1.0  # angular_velocity 上限

_VELOCITY_TABLE = {
    "stop":           {"forward": 0.0, "lateral": 0.0, "angular": 0.0},
    "forward":        {"forward": None, "lateral": 0.0, "angular": 0.0},
    "backward":       {"forward": None, "lateral": 0.0, "angular": 0.0},
    "left":           {"forward": 0.0, "lateral": 0.0, "angular": 0.9},
    "right":          {"forward": 0.0, "lateral": 0.0, "angular": -0.9},
    "left_forward":   {"forward": None, "lateral": 0.0, "angular": 0.9},
    "right_forward":  {"forward": None, "lateral": 0.0, "angular": -0.9},
    # 精确转向（角度 + 方向）
    "turn_left_45":  {"forward": 0.0, "lateral": 0.0, "angular": _MAX_ANGULAR, "turn_deg": _DEG45},
    "turn_right_45": {"forward": 0.0, "lateral": 0.0, "angular": -_MAX_ANGULAR, "turn_deg": _DEG45},
    "turn_left_90":  {"forward": 0.0, "lateral": 0.0, "angular": _MAX_ANGULAR, "turn_deg": _DEG90},
    "turn_right_90": {"forward": 0.0, "lateral": 0.0, "angular": -_MAX_ANGULAR, "turn_deg": _DEG90},
}


def _build_payload(forward: float, lateral: float, angular: float) -> dict:
    """构造与原项目调通格式一致的运动 payload。"""
    data = {"mode": 0, "forward_velocity": forward, "lateral_velocity": lateral}
    if angular != 0.0:
        data["angular_velocity"] = angular
    return {
        "header": {
            "timestamp": {"seconds": 0, "nanos": 0, "ms_since_epoch": 0},
            "control_source": "ControlSource_MANUAL",
        },
        "data": data,
    }


async def _send(forward: float, lateral: float, angular: float) -> bool:
    res = await get_client().post(MOTION_BASE, _build_payload(forward, lateral, angular))
    return res.ok


async def _stop() -> None:
    await _send(0.0, 0.0, 0.0)


@tool(
    name="move",
    description=(
        "控制机器人移动方向和步数/距离。直线运动走N步或指定距离后自动停止，"
        "转向类用角度精确控制（左转45°、左转90°、右转45°、右转90°）。"
        "速度默认0.8m/s，用户要求快时提高到0.9m/s。"
    ),
    properties={
        "direction": {
            "type": "string",
            "enum": ["forward", "backward", "left", "right",
                     "left_forward", "right_forward",
                     "turn_left_45", "turn_right_45",
                     "turn_left_90", "turn_right_90",
                     "stop"],
            "description": (
                "forward=前进, backward=后退, "
                "left/right=原地小幅度转向(约80°), "
                "left_forward/right_forward=斜向前进, "
                "turn_left_45/turn_right_45=原地左/右转45°, "
                "turn_left_90/turn_right_90=原地左/右转90°, "
                "stop=停止"
            ),
        },
        "steps": {"type": "integer", "description": "走几步(步长约0.3米)，distance>0时忽略。", "default": 1},
        "distance": {"type": "number", "description": "走多少米，优先级高于steps。"},
        "speed": {"type": "number", "description": "速度m/s(默认0.8，最大0.9)。", "default": 0.8},
    },
    required=["direction"],
    concurrency=Concurrency.EXCLUSIVE,
)
async def move(
    direction: Literal["forward", "backward", "left", "right",
                       "left_forward", "right_forward",
                       "turn_left_45", "turn_right_45",
                       "turn_left_90", "turn_right_90",
                       "stop"] = "stop",
    steps: int = 1,
    distance: float = 0,
    speed: float = 0.8,
) -> ToolResult:
    speed = min(speed, 0.9)  # 安全上限
    if distance and distance > 0:
        steps = max(1, round(distance / STEP_LENGTH))

    if direction not in _VELOCITY_TABLE:
        return ToolResult.fail(f"未知方向: {direction}")

    spec = _VELOCITY_TABLE[direction]
    forward = speed if spec["forward"] is None else spec["forward"]
    if direction == "backward":
        forward = -speed
    angular = spec["angular"]

    if not await _send(forward, spec["lateral"], angular):
        return ToolResult.fail("移动指令发送失败")

    # stop 或无需移动：直接返回
    if direction == "stop" or steps <= 0:
        return ToolResult.success(f"{direction} 指令已发送", direction=direction)

    # 精确角度转向：turn_left_45 / turn_left_90 / turn_right_45 / turn_right_90
    turn_deg = spec.get("turn_deg", 0)
    if turn_deg > 0:
        # angular 已知为 ±1.0，duration = 弧度 / |angular|
        turn_duration = abs(turn_deg / angular)
        log.info("move 转向 %s, 角度=%.1f°, angular=%.1f, 时长=%.2fs",
                 direction, turn_deg * 180 / 3.14159, angular, turn_duration)
        await asyncio.sleep(turn_duration)
        await _stop()
        return ToolResult.success(f"{direction} 已转向", direction=direction)

    # 旧版小幅度转向（无精确角度，用 80°）
    if angular != 0 and forward == 0:
        turn_duration = abs(80 / (abs(angular) * 180 / 3.14159))
        log.info("move 转向 %s, 时长=%.1fs", direction, turn_duration)
        await asyncio.sleep(turn_duration)
        await _stop()
        return ToolResult.success(f"{direction} 已转向", direction=direction)

    # 直线（或斜向）：按时长走完再停
    duration = steps * STEP_LENGTH / speed
    log.info("move %s 速度=%.2f 步数=%d 时长=%.1fs", direction, speed, steps, duration)
    await asyncio.sleep(duration)
    await _stop()
    dist_msg = f"{distance}m" if distance and distance > 0 else f"{steps}步"
    return ToolResult.success(f"{direction} 走了 {dist_msg}，已停止", direction=direction)
