"""
端到端：SessionStateProcessor 驱动任务中断流程（注入 mock 任务管理器）。
验证帧路由：AWAITING 时拦截转写、OTHER 下推 LLM、RESUME/ABANDON 不下推、答完触发追问。
运行: python -m tests.test_interrupt_e2e
"""
import asyncio
from core.result import ToolResult
from pipecat.frames.frames import (
    TranscriptionFrame, BotStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipeline.audio.session_processor import SessionStateProcessor
from pipeline.audio.task_interrupt import TaskInterruptManager, Phase


def make_proc(intent_holder):
    said, actions = [], []
    async def pause(t): actions.append(("pause", t)); return ToolResult.success()
    async def resume(t): actions.append(("resume", t)); return ToolResult.success()
    async def stop(t): actions.append(("stop", t)); return ToolResult.success()
    async def say(t): said.append(t)
    async def classify(t): return intent_holder["v"]
    tm = TaskInterruptManager(60, pause, resume, stop, say, classify)
    p = SessionStateProcessor(idle_timeout=5, set_light=lambda x: _noop(),
                              wakeup_tts=None, task_manager=tm)
    p._loop = asyncio.get_running_loop()
    # 记录被下推到 LLM 的转写
    pushed = []
    async def fake_push(frame, direction=FrameDirection.DOWNSTREAM):
        if isinstance(frame, TranscriptionFrame):
            pushed.append(frame.text)
    p.push_frame = fake_push
    return p, said, actions, pushed


async def _noop(): pass


async def t_other_pushes_to_llm():
    """AWAITING 时回别的问题(OTHER) → 转写下推 LLM 让它回答。"""
    holder = {"v": "OTHER"}
    p, said, actions, pushed = make_proc(holder)
    await p._task_mgr.on_wakeup_interrupt("15", "接人任务")
    await p._task_mgr.on_answer_complete()      # 进入 AWAITING
    assert p._task_mgr.phase == Phase.AWAITING
    await p.process_frame(TranscriptionFrame(text="那个展品多少年了", user_id="u", timestamp="t"),
                          FrameDirection.DOWNSTREAM)
    assert "那个展品多少年了" in pushed          # 下推给 LLM 了
    assert p._task_mgr.has_suspended             # 任务仍挂起
    assert ("stop", "15") not in actions
    print("  ✓ AWAITING+OTHER → 转写下推 LLM，任务保持挂起")


async def t_resume_not_pushed():
    """RESUME → 不下推 LLM（由任务管理器处理恢复）。"""
    holder = {"v": "RESUME"}
    p, said, actions, pushed = make_proc(holder)
    await p._task_mgr.on_wakeup_interrupt("15", "接人任务")
    await p._task_mgr.on_answer_complete()
    await p.process_frame(TranscriptionFrame(text="继续", user_id="u", timestamp="t"),
                          FrameDirection.DOWNSTREAM)
    assert "继续" not in pushed                  # 没下推给 LLM
    assert ("resume", "15") in actions
    assert not p._task_mgr.has_suspended
    print("  ✓ AWAITING+RESUME → 不下推 LLM，直接恢复任务")


async def t_abandon_not_pushed():
    holder = {"v": "ABANDON"}
    p, said, actions, pushed = make_proc(holder)
    await p._task_mgr.on_wakeup_interrupt("15", "接人任务")
    await p._task_mgr.on_answer_complete()
    await p.process_frame(TranscriptionFrame(text="不做了", user_id="u", timestamp="t"),
                          FrameDirection.DOWNSTREAM)
    assert "不做了" not in pushed
    assert ("stop", "15") in actions
    print("  ✓ AWAITING+ABANDON → 不下推 LLM，终止任务")


async def main():
    await t_other_pushes_to_llm()
    await t_resume_not_pushed()
    await t_abandon_not_pushed()
    print("\n✅ 任务中断端到端（帧路由）全部通过")


if __name__ == "__main__":
    asyncio.run(main())
