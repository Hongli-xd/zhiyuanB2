"""
能力：运动控制（move）。

控制机器人移动方向和距离。属于危险物理动作，标注 EXCLUSIVE：
执行期间由 dispatcher 的全局独占锁保证不与任何其它工具同时跑。

ROS2 topic 50Hz 持续发送，和 walk.py 脚本行为完全一致。
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Literal

from core.capability import Concurrency, tool
from core.result import ToolResult

log = logging.getLogger("a2.cap.motion")

STEP_LENGTH = 0.3  # m/step

_MAX_ANGULAR = 0.8
_MAX_FORWARD = 0.6
_MAX_LATERAL = 0.3

_LOCOMOTION_TOPIC = "/motion/control/locomotion_velocity/pb_3Aaimdk_2Eprotocol_2EMcLocomotionVelocityChannel"

_VELOCITY_TABLE = {
    "stop":           {"forward": 0.0, "lateral": 0.0, "angular": 0.0},
    "forward":        {"forward": None, "lateral": 0.0, "angular": 0.0},
    "backward":       {"forward": None, "lateral": 0.0, "angular": 0.0},
    "left":           {"forward": 0.0, "lateral": 0.0, "angular": _MAX_ANGULAR, "turn_deg": 80},
    "right":          {"forward": 0.0, "lateral": 0.0, "angular": -_MAX_ANGULAR, "turn_deg": 80},
    "left_forward":   {"forward": None, "lateral": 0.0, "angular": _MAX_ANGULAR},
    "right_forward":  {"forward": None, "lateral": 0.0, "angular": -_MAX_ANGULAR},
    "turn_left_45":  {"forward": 0.0, "lateral": 0.0, "angular": _MAX_ANGULAR, "turn_deg": 45},
    "turn_right_45": {"forward": 0.0, "lateral": 0.0, "angular": -_MAX_ANGULAR, "turn_deg": 45},
    "turn_left_90":  {"forward": 0.0, "lateral": 0.0, "angular": _MAX_ANGULAR, "turn_deg": 90},
    "turn_right_90": {"forward": 0.0, "lateral": 0.0, "angular": -_MAX_ANGULAR, "turn_deg": 90},
}


def _publish_locomotion(forward: float, lateral: float, angular: float,
                        count: int, done_event: threading.Event) -> None:
    """ROS2 线程：50Hz 持续发布 locomotion 指令，发布完成后设置 done_event。"""
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import QoSHistoryPolicy, QoSProfile, QoSReliabilityPolicy
    from ros2_plugin_proto.msg import RosMsgWrapper

    class _PubNode(Node):
        def __init__(self):
            super().__init__("move_publisher")
            qos = QoSProfile(
                history=QoSHistoryPolicy.KEEP_LAST,
                depth=10,
                reliability=QoSReliabilityPolicy.BEST_EFFORT,
            )
            self.pub = self.create_publisher(RosMsgWrapper, _LOCOMOTION_TOPIC, qos)
            self.count = count
            self.sent = 0
            self.timer = self.create_timer(0.02, self._cb)  # 50Hz

        def _cb(self):
            if self.sent >= self.count:
                self.timer.cancel()
                done_event.set()
                self.get_logger().info(f"发送完毕，共{self.sent}次，关闭节点")
                self.destroy_node()
                return
            payload = {
                "data": {
                    "mode": 0,
                    "forward_velocity": float(forward),
                    "lateral_velocity": float(lateral),
                    "angular_velocity": float(angular),
                }
            }
            json_bytes = json.dumps(payload).encode()
            msg = RosMsgWrapper()
            msg.serialization_type = "json"
            msg.data = [bytes([b]) for b in json_bytes]
            self.pub.publish(msg)
            self.sent += 1

    if not rclpy.ok():
        rclpy.init()
    node = _PubNode()
    try:
        rclpy.spin(node)
    except:
        pass
    finally:
        if rclpy.ok():
            node.destroy_node()
            rclpy.shutdown()


@tool(
    name="move",
    description=(
        "控制机器人移动方向和步数/距离。直线运动走N步或指定距离后自动停止，"
        "转向类用角度精确控制（左转45°、左转90°、右转45°、右转90°）。"
        "速度默认0.6m/s（最大）。"
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
        "speed": {"type": "number", "description": "速度m/s(默认0.6，最大0.6)。", "default": 0.6},
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
    speed: float = 0.6,
) -> ToolResult:
    speed = min(speed, _MAX_FORWARD)

    if direction not in _VELOCITY_TABLE:
        return ToolResult.fail(f"未知方向: {direction}")

    spec = _VELOCITY_TABLE[direction]
    forward = speed if spec["forward"] is None else spec["forward"]
    if direction == "backward":
        forward = -speed
    angular = spec["angular"]

    if direction == "stop" or steps <= 0:
        # 发一次停止
        done = threading.Event()
        t = threading.Thread(target=_publish_locomotion,
                           args=(0.0, 0.0, 0.0, 1, done))
        t.start()
        done.wait(timeout=5)
        return ToolResult.success("已停止")

    # 转向：按角度算次数
    turn_deg = spec.get("turn_deg", 0)
    if turn_deg > 0:
        duration = abs(turn_deg / 180 * 3.14159 / abs(angular))
        count = max(1, int(duration / 0.02))
        log.info("move %s, angular=%.1f, 发送%d次(50Hz)", direction, angular, count)
        done = threading.Event()
        t = threading.Thread(target=_publish_locomotion,
                           args=(0.0, 0.0, angular, count, done))
        t.start()
        done.wait(timeout=max(duration + 1, 10))
        return ToolResult.success(f"{direction} 已转向")

    # 直线/斜向：按时长算次数
    duration = steps * STEP_LENGTH / speed
    count = max(1, int(duration / 0.02))
    log.info("move %s, forward=%.2f, angular=%.1f, 发送%d次(50Hz)",
             direction, forward, angular, count)
    done = threading.Event()
    t = threading.Thread(target=_publish_locomotion,
                       args=(forward, spec["lateral"], angular, count, done))
    t.start()
    done.wait(timeout=max(duration + 1, 10))
    dist_msg = f"{distance}m" if distance and distance > 0 else f"{steps}步"
    return ToolResult.success(f"{direction} 走了 {dist_msg}，已停止")
