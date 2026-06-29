"""
ROS2 音频输入处理器。

直接处理 VAD 状态（对齐 zhiyuanA 的 ros_audio_input.py 逻辑）：
  vad=1 → 清 buffer，开始累积
  vad=2 → 持续累积
  vad=3 → 累积最后一个片段，一起送 ASR

唤醒 / 空闲超时 / 灯带由 SessionStateProcessor 统一负责。
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
from pipeline.audio.session_processor import SessionStateProcessor


class ROS2AudioInputProcessor(FrameProcessor):
    def __init__(self, session: SessionStateProcessor):
        super().__init__()
        self._session = session
        self._asr = build_asr()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False
        self.log = logging.getLogger("a2.ros_audio")

        # 音频缓冲（对齐 zhiyuanA）
        self._audio_buffer = bytearray()
        self._is_recording = False
        self.log.info("ROS2AudioInputProcessor 初始化完成")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        self.log.info("[process_frame] 入: frame=%s, direction=%s", type(frame).__name__, direction)
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self.log.info("[process_frame] StartFrame，开始启动 ROS 线程")
            self._loop = asyncio.get_running_loop()
            self._start_ros()
            self.log.info("[process_frame] 等待唤醒...")
        elif isinstance(frame, EndFrame):
            self.log.info("[process_frame] EndFrame，设置 _running=False")
            self._running = False
        await self.push_frame(frame, direction)
        self.log.info("[process_frame] 出")

    # ── ROS 线程 ──────────────────────────────────────────────────────────
    def _start_ros(self):
        self.log.info("[_start_ros] 启动 ROS 线程")
        self._running = True
        t = threading.Thread(target=self._ros_spin, daemon=True)
        t.start()
        self.log.info("[_start_ros] 线程已启动")

    def _ros_spin(self):
        self.log.info("[_ros_spin] ROS 线程开始")
        try:
            import rclpy
            from rclpy.node import Node
            from rclpy.qos import (QoSProfile, QoSHistoryPolicy,
                                   QoSReliabilityPolicy, QoSDurabilityPolicy)
        except ImportError:
            self.log.error("[_ros_spin] 未安装 rclpy，需在 A2 机器人环境运行。")
            return

        if not rclpy.ok():
            self.log.info("[_ros_spin] rclpy 未初始化，执行 init")
            rclpy.init()
        else:
            self.log.info("[_ros_spin] rclpy 已初始化")

        parent = self

        class _DualNode(Node):
            def __init__(self):
                super().__init__("a2_agent_dual_node")
                from ros2_plugin_proto.msg import RosMsgWrapper
                self.create_subscription(
                    RosMsgWrapper,
                    "/agent/wakeup/pb_3Aaimdk_2Eprotocol_2EWakeUpResult",
                    self._on_wakeup,
                    QoSProfile(reliability=QoSReliabilityPolicy.BEST_EFFORT,
                               durability=QoSDurabilityPolicy.VOLATILE, depth=10),
                )
                self._audio_subscribed = False
                parent.log.info("[_ros_spin] 唤醒订阅已创建，音频订阅待唤醒后建立")

            def _on_wakeup(self, _msg):
                parent.log.info("[_on_wakeup] 收到唤醒消息")
                if not self._audio_subscribed:
                    self._audio_subscribed = True
                    from ros2_plugin_proto.msg import RosMsgWrapper
                    self.create_subscription(
                        RosMsgWrapper, config.AUDIO_TOPIC, self._on_audio,
                        QoSProfile(history=QoSHistoryPolicy.KEEP_LAST, depth=10,
                                   reliability=QoSReliabilityPolicy.BEST_EFFORT,
                                   durability=QoSDurabilityPolicy.TRANSIENT_LOCAL),
                    )
                    parent.log.info("[_on_wakeup] 音频订阅已建立")
                else:
                    parent.log.info("[_on_wakeup] 音频订阅已存在，跳过")
                parent._session.notify_wakeup()

            def _on_audio(self, msg):
                data_len = len(msg.data) if hasattr(msg, 'data') else -1
                parent.log.info("[_on_audio] 收到音频消息 data_len=%d", data_len)
                # 打印当前状态
                parent.log.info("[_on_audio] 当前 state=%s", parent._session.session.state.value)
                parent._handle_audio(msg)

        node = _DualNode()
        spin_count = 0
        while self._running and rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0.1)
            spin_count += 1
            if spin_count % 50 == 0:
                parent.log.debug("[_ros_spin] spin alive, count=%d", spin_count)
        parent.log.info("[_ros_spin] ROS 线程退出, total_spins=%d", spin_count)

    # ── 音频处理（直接处理 VAD 状态，不用 VadBuffer）───────────────────────
    def _handle_audio(self, msg):
        self.log.info("[_handle_audio] 入")
        # 状态门：只有 LISTENING/PROCESSING 才处理
        should = self._session.session.should_process_audio()
        state = self._session.session.state
        self.log.info("[_handle_audio] should_process=%s, state=%s", should, state.value)
        if not should:
            self.log.info("[_handle_audio] 丢弃，state=%s 不处理", state.value)
            return

        # 解析 protobuf
        try:
            ser_type = getattr(msg, "serialization_type", "unknown")
            self.log.info("[_handle_audio] serialization_type=%s", ser_type)
            if ser_type != "pb":
                self.log.info("[_handle_audio] 非pb格式，跳过")
                return
            from aimdk.protocol_pb2 import ProcessedAudioOutput
            result = ProcessedAudioOutput()
            result.ParseFromString(b"".join(msg.data))
            stream_id = result.stream_id
            vad_state = result.vad_state
            audio_data = bytes(result.audio_data)
            self.log.info("[_handle_audio] 解析成功: stream_id=%d, vad_state=%d, audio_len=%d",
                         stream_id, vad_state, len(audio_data))
            if stream_id != 1:
                self.log.info("[_handle_audio] stream_id=%d 不是板载麦克风，跳过", stream_id)
                return
        except Exception as e:
            self.log.error("[_handle_audio] 解析失败: %s", e)
            return

        # VAD 状态机（对齐 zhiyuanA）
        self.log.info("[_handle_audio] VAD: vad=%d, _is_recording=%s, buf_len=%d",
                     vad_state, self._is_recording, len(self._audio_buffer))

        if vad_state == 1:  # 语音开始
            self.log.info("[_handle_audio] VAD=BEGIN 清buffer，开始累积")
            self._audio_buffer.clear()
            self._is_recording = True
            if audio_data:
                self._audio_buffer.extend(audio_data)
            self.log.info("[_handle_audio] 提交 UserStartedSpeakingFrame")
            self._submit(self._emit(UserStartedSpeakingFrame()))
            self.log.info("[_handle_audio] VAD=BEGIN 处理完成")

        elif vad_state == 2:  # 语音中
            if self._is_recording and audio_data:
                self._audio_buffer.extend(audio_data)
                self.log.info("[_handle_audio] VAD=SPEAKING 累积 audio, buf_len=%d", len(self._audio_buffer))
            else:
                self.log.info("[_handle_audio] VAD=SPEAKING 但 _is_recording=False 或 audio_data 空，跳过")

        elif vad_state == 3:  # 语音结束
            if self._is_recording and audio_data:
                self._audio_buffer.extend(audio_data)
                self.log.info("[_handle_audio] VAD=END 累积最后片段, buf_len=%d", len(self._audio_buffer))
            self._is_recording = False
            total_size = len(self._audio_buffer)
            self.log.info("[_handle_audio] VAD=END, 总大小=%d bytes", total_size)

            if total_size < 6400:
                self._audio_buffer.clear()
                self.log.info("[_handle_audio] 语音太短(<6400bytes)，跳过")
                return

            self.log.info("[_handle_audio] 提交 UserStoppedSpeakingFrame")
            self._submit(self._emit(UserStoppedSpeakingFrame()))
            audio = bytes(self._audio_buffer)
            self._audio_buffer.clear()
            self.log.info("[_handle_audio] 提交 _transcribe_and_push, audio_size=%d", len(audio))
            self._submit(self._transcribe_and_push(audio))
            self.log.info("[_handle_audio] VAD=END 处理完成")

        elif vad_state == 0:  # 静默
            if self._is_recording:
                self._is_recording = False
                self.log.info("[_handle_audio] VAD=SILENCE 重置 _is_recording=False")
            else:
                self.log.info("[_handle_audio] VAD=SILENCE，_is_recording 已是 False")

        self.log.info("[_handle_audio] 出")

    def _submit(self, coro):
        self.log.info("[_submit] 入, loop=%s", self._loop)
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._loop)
            self.log.info("[_submit] 已提交到事件循环")
        else:
            self.log.warning("[_submit] 丢弃，loop=%s", self._loop)

    async def _emit(self, frame: Frame):
        self.log.info("[_emit] 入: %s", type(frame).__name__)
        await self.push_frame(frame, FrameDirection.DOWNSTREAM)
        self.log.info("[_emit] 出: %s", type(frame).__name__)

    async def _transcribe_and_push(self, pcm: bytes):
        self.log.info("[_transcribe_and_push] 入, pcm_size=%d bytes", len(pcm))
        self.log.info("[_transcribe_and_push] 调用 ASR...")
        text, _conf = await self._asr.transcribe(pcm)
        self.log.info("[_transcribe_and_push] ASR 返回: 「%s」", text if text else "(空)")
        if not text:
            self.log.info("[_transcribe_and_push] ASR 空，返回")
            return
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.log.info("[_transcribe_and_push] 推 TranscriptionFrame: 「%s」", text)
        await self.push_frame(
            TranscriptionFrame(text=text, user_id="user", timestamp=ts),
            FrameDirection.DOWNSTREAM,
        )
        self.log.info("[_transcribe_and_push] 出")
