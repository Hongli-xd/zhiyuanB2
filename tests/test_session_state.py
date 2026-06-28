"""
SessionState 纯逻辑单测：状态转换 + 计时器回调时机（无 pipecat / 无 ROS）。
运行: python -m tests.test_session_state
"""

from pipeline.audio.session_state import SessionState, State


class Recorder:
    """记录回调调用，便于断言。"""
    def __init__(self):
        self.transitions = []
        self.arm = 0
        self.cancel = 0

    def make(self):
        return SessionState(
            idle_timeout=60,
            on_transition=lambda o, n: self.transitions.append((o.value, n.value)),
            on_arm_timer=lambda: setattr(self, "arm", self.arm + 1),
            on_cancel_timer=lambda: setattr(self, "cancel", self.cancel + 1),
        )


def test_initial_sleeping():
    s = Recorder().make()
    assert s.state == State.SLEEPING
    assert not s.should_process_audio()  # 休眠时不处理音频


def test_wakeup_to_listening_arms_timer():
    r = Recorder(); s = r.make()
    s.on_wakeup()
    assert s.state == State.LISTENING
    assert s.should_process_audio()
    assert r.arm == 1                       # 唤醒启动空闲计时器
    assert r.transitions == [("sleeping", "listening")]


def test_full_turn_continuous_dialog():
    """唤醒 → 说话 → 处理 → 回复完成 → 回 LISTENING（连续多轮，不回休眠）。"""
    r = Recorder(); s = r.make()
    s.on_wakeup()                # → LISTENING, arm=1
    s.on_speech_activity()       # 重置计时器 arm=2
    s.on_transcription()         # → PROCESSING, cancel=1
    assert s.state == State.PROCESSING
    s.on_turn_complete()         # → LISTENING, arm=3
    assert s.state == State.LISTENING   # 关键：回 LISTENING 而非 SLEEPING
    assert r.arm == 3 and r.cancel == 1


def test_processing_cancels_idle_timer():
    """进入 PROCESSING 必须取消空闲计时器（长任务不误判空闲）。"""
    r = Recorder(); s = r.make()
    s.on_wakeup()
    s.on_transcription()
    assert r.cancel == 1
    # 处理期间即便空闲计时器到点也不应生效（状态不是 LISTENING）
    s.idle_timeout_fired()
    assert s.state == State.PROCESSING   # 没被踢回休眠


def test_idle_timeout_back_to_sleeping():
    r = Recorder(); s = r.make()
    s.on_wakeup()                # LISTENING
    s.idle_timeout_fired()       # 静默到点 → SLEEPING
    assert s.state == State.SLEEPING
    assert not s.should_process_audio()
    assert ("listening", "sleeping") in r.transitions


def test_interrupt_back_to_listening():
    r = Recorder(); s = r.make()
    s.on_wakeup(); s.on_transcription()   # PROCESSING
    s.on_interrupt()                       # 打断 → LISTENING
    assert s.state == State.LISTENING


def test_idle_fired_in_sleeping_is_noop():
    """已休眠时计时器回调不应有副作用。"""
    r = Recorder(); s = r.make()
    s.idle_timeout_fired()
    assert s.state == State.SLEEPING
    assert r.transitions == []


def test_second_day_no_wakeup_stays_sleeping():
    """模拟第二天：长时间后，未唤醒则保持 SLEEPING，音频被门控丢弃。"""
    r = Recorder(); s = r.make()
    s.on_wakeup()
    s.idle_timeout_fired()       # 超时回休眠
    assert s.state == State.SLEEPING
    # 第二天直接说话（不唤醒）：should_process_audio 为 False → 音频被丢
    assert not s.should_process_audio()


if __name__ == "__main__":
    import sys
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ✓ {t.__name__}")
    print(f"\n✅ SessionState 全部 {len(tests)} 项通过")
