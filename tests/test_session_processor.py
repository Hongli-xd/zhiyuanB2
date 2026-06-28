"""
SessionStateProcessor 集成测试：真正的 asyncio 空闲计时器 + 灯带副作用。

pipecat 的 process_frame 需要完整 TaskManager 才能跑，单测里不便搭建；
但帧的作用就是调用 session 的事件方法，所以这里直接驱动这些方法 + 真实计时器，
验证「计时器在无音频时触发」「活动重置」「PROCESSING 不超时」「灯带随状态切」。

帧→方法映射（与 process_frame 内一致）：
  UserStartedSpeakingFrame → on_speech_activity
  TranscriptionFrame       → on_transcription
  BotStoppedSpeakingFrame  → on_turn_complete
  StartInterruptionFrame   → on_interrupt

运行: python -m tests.test_session_processor
"""

import asyncio

from pipeline.audio.session_processor import SessionStateProcessor
from pipeline.audio.session_state import State


def _make(idle=0.3):
    lights = []
    async def fake_light(preset): lights.append(preset)
    p = SessionStateProcessor(idle_timeout=idle, set_light=fake_light, wakeup_tts=None)
    p._loop = asyncio.get_running_loop()   # 注入事件循环（正常由 StartFrame 注入）
    return p, lights


async def t_timer_fires_without_audio():
    """核心 bug 修复：唤醒后不发任何音频帧，空闲计时器到点自动回 SLEEPING。"""
    p, lights = _make(idle=0.3)
    p._handle_wakeup()                       # → LISTENING + 启动真实计时器
    assert p.session.state == State.LISTENING
    await asyncio.sleep(0.5)                  # 纯等，不发帧
    assert p.session.state == State.SLEEPING, "计时器未在无音频时触发!"
    await asyncio.sleep(0)                    # 让灯带 task 跑完
    assert lights[-1] == "off"
    print("  ✓ 无音频帧时空闲计时器仍触发（原 bug 已修复）")


async def t_activity_resets_timer():
    p, _ = _make(idle=0.3)
    p._handle_wakeup()
    for _ in range(4):                        # 每 0.15s 活动一次，累计 0.6s > idle
        await asyncio.sleep(0.15)
        p.session.on_speech_activity()
    assert p.session.state == State.LISTENING, "活动期间不应休眠"
    print("  ✓ 说话活动重置计时器，连续期间不休眠")


async def t_processing_no_idle_timeout():
    p, _ = _make(idle=0.3)
    p._handle_wakeup()
    p.session.on_transcription()             # → PROCESSING，计时器取消
    assert p.session.state == State.PROCESSING
    await asyncio.sleep(0.5)
    assert p.session.state == State.PROCESSING, "PROCESSING 不该被空闲打断"
    print("  ✓ PROCESSING 期间不被空闲计时器打断")


async def t_full_continuous_dialog():
    p, lights = _make(idle=5)
    p._handle_wakeup()                        # waiting
    await asyncio.sleep(0)
    p.session.on_transcription()              # working
    await asyncio.sleep(0)
    assert p.session.state == State.PROCESSING
    p.session.on_turn_complete()              # 回 waiting
    await asyncio.sleep(0)
    assert p.session.state == State.LISTENING  # 连续多轮
    assert "waiting" in lights and "working" in lights
    print("  ✓ 完整一轮后回 LISTENING（连续多轮）+ 灯带随状态切换")


async def t_interrupt():
    p, _ = _make(idle=5)
    p._handle_wakeup()
    p.session.on_transcription()
    assert p.session.state == State.PROCESSING
    p.session.on_interrupt()
    assert p.session.state == State.LISTENING
    print("  ✓ 用户插话打断 → 回 LISTENING")


async def t_resleep_then_wakeup_again():
    """休眠后再次唤醒能正常复活（第二天场景：必须唤醒才行，唤醒后恢复）。"""
    p, _ = _make(idle=0.2)
    p._handle_wakeup()
    await asyncio.sleep(0.35)                  # 超时休眠
    assert p.session.state == State.SLEEPING
    p._handle_wakeup()                         # 重新唤醒
    assert p.session.state == State.LISTENING
    print("  ✓ 休眠后重新唤醒可正常复活")


async def main():
    for t in [t_timer_fires_without_audio, t_activity_resets_timer,
              t_processing_no_idle_timeout, t_full_continuous_dialog,
              t_interrupt, t_resleep_then_wakeup_again]:
        await t()
        p_cancel = await asyncio.sleep(0.05)
    print("\n✅ SessionStateProcessor 集成测试全部通过")


if __name__ == "__main__":
    asyncio.run(main())
