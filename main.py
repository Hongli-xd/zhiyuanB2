"""
A2 语音 Agent 主入口（在机器人上运行）。

链路：ROS音频 → 会话状态机 → ASR → LLM(+工具) → A2 TTS
会话状态(唤醒/连续多轮/60s静默休眠/灯带提示)由 SessionStateProcessor 统一管理。

用法: python -m main
"""

import asyncio
import logging
import sys

from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask

from pipeline.build import build_pipeline
from pipeline.audio.ros_source import ROS2AudioInputProcessor
from services.a2_client import a2_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("a2.main")


async def run():
    await a2_client.start()

    # 工厂：把 session 注入 ROS 音频源（音频门控 + 唤醒通知都靠它）
    pipeline, _llm, _session = build_pipeline(
        lambda session: ROS2AudioInputProcessor(session)
    )

    task = PipelineTask(pipeline, cancel_on_idle_timeout=False, enable_turn_tracking=False)
    runner = PipelineRunner()

    log.info("A2 语音 Agent 启动，等待唤醒… (Ctrl+C 退出)")
    try:
        await runner.run(task)
    finally:
        await a2_client.stop()


def main():
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("收到退出信号，已停止")


if __name__ == "__main__":
    main()
