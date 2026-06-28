"""
ROS2 音频输入处理器（清理版）。

职责单一：订阅 A2 麦克风/唤醒 topic，解 protobuf，喂给 VadBuffer，
语音结束后调 ASR 产出 TranscriptionFrame 推入 pipeline。

状态管理已抽离：唤醒 / 空闲超时 / 灯带由 SessionStateProcessor 统一负责。
本类只做两件与状态相关的事：
  - 收到唤醒消息 → 通知 session.notify_wakeup()（跨线程安全）
  - 处理音频帧前 → 问 session.should_process_audio() 决定是否处理
这样状态逻辑集中一处、可单测，本类回归「音频管道」的单一职责。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Optional

from pipecat.frames.frames import (
    Frame, StartFrame, EndFrame, TranscriptionFrame,
    UserStartedSpeakingFrame, UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

import config
from services.asr import build_asr
from pipeline.audio.vad_buffer import VadBuffer
from pipeline.audio.session_processor import SessionStateProcessor


class ROS2AudioInputProcessor(FrameProcessor):
    def __init__(self, session: SessionStateProcessor):
        super().__init__()
        self._session = session
        self._asr = build_asr()
        self._vad = VadBuffer()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False
        self.log = logging.getLogger("a2.ros_audio")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self._loop = asyncio.get_running_loop()
            self._start_ros()
            self.log.info("等待唤醒...")
        elif isinstance(frame, EndFrame):
            self._running = False
        await self.push_frame(frame, direction)

    # ── ROS 线程 ──────────────────────────────────────────────────────────
    def _start_ros(self):
        self._running = True
        threading.Thread(target=self._ros_spin, daemon=True).start()

    def _ros_spin(self):
        try:
            import rclpy
            from rclpy.node import Node
            from rclpy.qos import (QoSProfile, QoSHistoryPolicy,
                                   QoSReliabilityPolicy, QoSDurabilityPolicy)
        except ImportError:
            self.log.error("未安装 rclpy，需在 A2 机器人环境运行。")
            return

        if not rclpy.ok():
            rclpy.init()
        parent = self

        class _DualNode(Node):
            def __init__(self):
                super().__init__("a2_agent_dual_node")
                from ros2_plugin_proto.msg import RosMsgWrapper
                # 唤醒 topic 用 VOLATILE，匹配 embodied_agent 的发布端
                self.create_subscription(
                    RosMsgWrapper,
                    "/agent/wakeup/pb_3Aaimdk_2Eprotocol_2EWakeUpResult",
                    self._on_wakeup,
                    QoSProfile(reliability=QoSReliabilityPolicy.BEST_EFFORT,
                               durability=QoSDurabilityPolicy.VOLATILE, depth=10),
                )
                self._audio_subscribed = False
                parent.log.info("唤醒订阅已创建，音频订阅待唤醒后建立")

            def _on_wakeup(self, _msg):
                parent.log.info("🔔 _on_wakeup 触发")
                # 首次唤醒后建立音频订阅（延迟订阅避免 DDS 匹配失败，见 issues.md #1）
                if not self._audio_subscribed:
                    self._audio_subscribed = True
                    from ros2_plugin_proto.msg import RosMsgWrapper
                    self.create_subscription(
                        RosMsgWrapper, config.AUDIO_TOPIC, self._on_audio,
                        QoSProfile(history=QoSHistoryPolicy.KEEP_LAST, depth=10,
                                   reliability=QoSReliabilityPolicy.BEST_EFFORT,
                                   durability=QoSDurabilityPolicy.TRANSIENT_LOCAL),
                    )
                    parent.log.info("音频订阅已建立")
                # 状态转换交给 session（跨线程安全）
                parent._session.notify_wakeup()

            def _on_audio(self, msg):
                parent._handle_audio(msg)

        node = _DualNode()
        while self._running and rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0.1)

    # ── 音频处理 ──────────────────────────────────────────────────────────
    def _handle_audio(self, msg):
        # 状态门：只有 LISTENING/PROCESSING 才处理（SLEEPING 时丢弃）
        should = self._session.session.should_process_audio()
        self.log.info("🎤 音频帧到达, should_process=%s, state=%s", should, self._session.session.state.value)
        if not should:
            return
        try:
            if getattr(msg, "serialization_type", "pb") != "pb":
                return
            from aimdk.protocol_pb2 import ProcessedAudioOutput
            result = ProcessedAudioOutput()
            result.ParseFromString(b"".join(msg.data))
            if result.stream_id != 1:  # 只处理板载麦克风
                return
            vad_state = result.vad_state
            audio = bytes(result.audio_data)
        except Exception as e:
            self.log.error("解析音频消息失败: %s", e)
            return

        event = self._vad.feed(vad_state, audio)
        self.log.info("🎤 VAD: state=%d, started=%s, stopped=%s, buf_len=%d",
                      vad_state, event.started, event.stopped, len(self._vad._buf))
        if event.started:
            self.log.info("🎤 语音开始")
            self._submit(self._emit(UserStartedSpeakingFrame()))
        if event.stopped:
            self.log.info("🎤 语音停止, utterance=%s", "有" if event.utterance else "无")
            self._submit(self._emit(UserStoppedSpeakingFrame()))
            if event.utterance:
                self.log.info("🎤 语音结束，送 ASR（%d bytes）", len(event.utterance))
                self._submit(self._transcribe_and_push(event.utterance))
            else:
                self.log.info("⏩ 语音过短，跳过")

    def _submit(self, coro):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _emit(self, frame: Frame):
        await self.push_frame(frame, FrameDirection.DOWNSTREAM)

    async def _transcribe_and_push(self, pcm: bytes):
        self.log.info("📝 ASR 收到音频 %d bytes，开始识别...", len(pcm))
        text, _conf = await self._asr.transcribe(pcm)
        self.log.info("📝 ASR 识别结果 -> 「%s」", text if text else "(空)")
        if not text:
            self.log.info("⏭️ ASR 未识别到文字")
            return
        self.log.info("📝 ASR 识别 -> 「%s」", text)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        await self.push_frame(
            TranscriptionFrame(text=text, user_id="user", timestamp=ts),
            FrameDirection.DOWNSTREAM,
        )
