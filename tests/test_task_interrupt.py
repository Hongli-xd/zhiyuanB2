"""
任务中断管理器测试——覆盖你描述的完整交互。
运行: python -m tests.test_task_interrupt
"""
import asyncio
from core.result import ToolResult
from pipeline.audio.task_interrupt import TaskInterruptManager, Phase


class Harness:
    """记录所有副作用调用，注入可控的分类结果。"""
    def __init__(self, resume_timeout=0.3, pause_ok=True, resume_ok=True):
        self.said = []
        self.actions = []          # (action, task_id)
        self.next_intent = "OTHER"
        self.pause_ok = pause_ok
        self.resume_ok = resume_ok

        async def pause(tid):
            self.actions.append(("pause", tid))
            return ToolResult.success() if self.pause_ok else ToolResult.fail("x")
        async def resume(tid):
            self.actions.append(("resume", tid))
            return ToolResult.success() if self.resume_ok else ToolResult.fail("x")
        async def stop(tid):
            self.actions.append(("stop", tid)); return ToolResult.success()
        async def say(t):
            self.said.append(t)
        async def classify(t):
            return self.next_intent

        self.mgr = TaskInterruptManager(
            resume_timeout=resume_timeout,
            pause_fn=pause, resume_fn=resume, stop_fn=stop,
            say_fn=say, classify_fn=classify,
        )


async def t_wakeup_pauses_task():
    h = Harness()
    ok = await h.mgr.on_wakeup_interrupt("15", "接人任务")
    assert ok and h.mgr.has_suspended
    assert ("pause", "15") in h.actions
    assert h.mgr.phase == Phase.ANSWERING
    print("  ✓ 唤醒打断 → 暂停任务并挂起")


async def t_pause_fail():
    h = Harness(pause_ok=False)
    ok = await h.mgr.on_wakeup_interrupt("15", "接人任务")
    assert not ok and not h.mgr.has_suspended
    assert any("没能暂停" in s for s in h.said)
    print("  ✓ 暂停失败 → 不挂起并告知用户")


async def t_answer_then_followup():
    h = Harness()
    await h.mgr.on_wakeup_interrupt("15", "接人任务")
    await h.mgr.on_answer_complete()       # 答完一个问题 → 追问
    assert h.mgr.phase == Phase.AWAITING
    assert any("要继续刚才的接人任务吗" in s for s in h.said)
    print("  ✓ 回答完 → 追问『要继续刚才的接人任务吗』")


async def t_resume():
    h = Harness(); h.next_intent = "RESUME"
    await h.mgr.on_wakeup_interrupt("15", "接人任务")
    await h.mgr.on_answer_complete()
    r = await h.mgr.on_user_reply("继续")
    assert r == "resumed"
    assert ("resume", "15") in h.actions
    assert not h.mgr.has_suspended           # 已清空
    assert any("继续接人任务" in s for s in h.said)
    print("  ✓ 用户说继续 → 恢复任务、清空挂起")


async def t_abandon():
    h = Harness(); h.next_intent = "ABANDON"
    await h.mgr.on_wakeup_interrupt("15", "接人任务")
    await h.mgr.on_answer_complete()
    r = await h.mgr.on_user_reply("不做了")
    assert r == "abandoned"
    assert ("stop", "15") in h.actions
    assert not h.mgr.has_suspended
    print("  ✓ 用户说放弃 → 终止任务、清空挂起")


async def t_defer_keeps_asking():
    """核心：『先不用』→ OTHER → 回答问题 → 再次追问，任务不丢。"""
    h = Harness(); h.next_intent = "OTHER"
    await h.mgr.on_wakeup_interrupt("15", "接人任务")
    await h.mgr.on_answer_complete()        # 第一次追问
    r = await h.mgr.on_user_reply("先不用")  # 推迟
    assert r == "answer"                     # 当作普通问题处理
    assert h.mgr.has_suspended               # 任务仍挂起，没被放弃!
    assert ("stop", "15") not in h.actions   # 绝对没 STOP
    assert h.mgr.phase == Phase.ANSWERING
    # 回答完这个新问题后，应再次追问
    await h.mgr.on_answer_complete()
    assert h.mgr.phase == Phase.AWAITING
    followups = [s for s in h.said if "要继续刚才的" in s]
    assert len(followups) == 2               # 追问了两次
    print("  ✓ 『先不用』→ 回答后再次追问，任务始终挂起（不误杀）")


async def t_silence_timeout_abandons():
    """追问后沉默超过阈值 → 自动放弃。"""
    h = Harness(resume_timeout=0.2)
    await h.mgr.on_wakeup_interrupt("15", "接人任务")
    await h.mgr.on_answer_complete()         # 追问 + 启动超时计时
    await asyncio.sleep(0.4)                  # 沉默超过 0.2s
    assert not h.mgr.has_suspended
    assert ("stop", "15") in h.actions
    print("  ✓ 追问后沉默超时 → 自动放弃任务")


async def t_reply_cancels_timeout():
    """超时前用户回复了 → 不触发自动放弃。"""
    h = Harness(resume_timeout=0.3); h.next_intent = "OTHER"
    await h.mgr.on_wakeup_interrupt("15", "接人任务")
    await h.mgr.on_answer_complete()
    await asyncio.sleep(0.1)
    await h.mgr.on_user_reply("那个展品是什么")  # 在超时前回复
    await asyncio.sleep(0.4)                       # 再等，确认原计时已取消
    assert h.mgr.has_suspended                     # 回复后任务仍在（OTHER）
    assert ("stop", "15") not in h.actions
    print("  ✓ 超时前回复 → 取消自动放弃计时")


async def main():
    for t in [t_wakeup_pauses_task, t_pause_fail, t_answer_then_followup,
              t_resume, t_abandon, t_defer_keeps_asking,
              t_silence_timeout_abandons, t_reply_cancels_timeout]:
        await t()
    print("\n✅ 任务中断管理器全部通过")


if __name__ == "__main__":
    asyncio.run(main())
