"""VadBuffer 纯逻辑单测（无需机器人环境）。运行: python -m tests.test_vad"""
from pipeline.audio.vad_buffer import VadBuffer, VadState


def test_full_utterance():
    vb = VadBuffer()
    assert vb.feed(VadState.BEGIN, b"\x01\x00" * 100).started
    vb.feed(VadState.SPEAKING, b"\x01\x00" * 2000)
    ev = vb.feed(VadState.END, b"\x01\x00" * 2000)
    assert ev.stopped and ev.utterance is not None
    assert len(ev.utterance) >= 6400


def test_too_short_dropped():
    vb = VadBuffer()
    vb.feed(VadState.BEGIN, b"\x01\x00" * 10)
    ev = vb.feed(VadState.END, b"\x01\x00" * 10)
    assert ev.stopped and ev.utterance is None  # 过短被丢弃


def test_silence_resets():
    vb = VadBuffer()
    vb.feed(VadState.BEGIN, b"\x00" * 100)
    ev = vb.feed(VadState.SILENCE, b"")
    assert not ev.started and not ev.stopped


if __name__ == "__main__":
    test_full_utterance()
    test_too_short_dropped()
    test_silence_resets()
    print("✅ VadBuffer 单测通过")
