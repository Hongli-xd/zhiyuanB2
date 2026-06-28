"""
意图三分类单测——重点验证「先不用 ≠ 放弃」。
运行: python -m tests.test_intent
"""
import asyncio
from capabilities.task.intent import classify, classify_keyword, RESUME, ABANDON, OTHER


def test_resume():
    for s in ["继续", "继续吧", "接着干", "好的继续执行", "恢复任务"]:
        assert classify_keyword(s) == RESUME, s


def test_abandon():
    for s in ["放弃", "取消任务", "不做了", "别做了", "终止吧", "算了不做"]:
        assert classify_keyword(s) == ABANDON, s


def test_defer_is_not_abandon():
    """核心：推迟类必须是 OTHER，绝不能是 ABANDON。"""
    for s in ["先不用", "先不用了", "等一下", "等会儿", "稍后", "待会再说",
              "先放着", "不急", "晚点再继续", "一会儿再说"]:
        result = classify_keyword(s)
        assert result == OTHER, f"'{s}' 被误判为 {result}，应为 OTHER"


def test_other_questions():
    """问别的问题 → 关键词判不了 → 返回 None（交给 LLM）。"""
    for s in ["今天天气怎么样", "你叫什么名字", "讲个笑话"]:
        assert classify_keyword(s) is None, s


def test_empty_is_other():
    assert classify_keyword("") == OTHER


async def test_llm_fallback():
    """无关键词命中时走 LLM 兜底；mock 一个 LLM。"""
    async def fake_llm(prompt):
        return "OTHER"
    r = await classify("今天天气怎么样", llm_call=fake_llm)
    assert r == OTHER

    async def fake_llm_resume(prompt):
        return "用户想继续，RESUME"
    r = await classify("那就开工吧", llm_call=fake_llm_resume)
    assert r == RESUME


async def test_defer_beats_llm():
    """即使有 LLM，'先不用' 也应被关键词层直接拦成 OTHER，不调 LLM。"""
    called = {"n": 0}
    async def spy_llm(prompt):
        called["n"] += 1
        return "ABANDON"
    r = await classify("先不用", llm_call=spy_llm)
    assert r == OTHER and called["n"] == 0, "先不用不该走到 LLM，且必须是 OTHER"


def main():
    test_resume(); print("  ✓ 明确继续 → RESUME")
    test_abandon(); print("  ✓ 明确放弃 → ABANDON")
    test_defer_is_not_abandon(); print("  ✓ 先不用/等会儿等推迟 → OTHER（不误杀任务）")
    test_other_questions(); print("  ✓ 别的问题 → 交给 LLM")
    test_empty_is_other(); print("  ✓ 空回复 → OTHER")
    asyncio.run(test_llm_fallback()); print("  ✓ LLM 兜底分类正常")
    asyncio.run(test_defer_beats_llm()); print("  ✓ '先不用' 被关键词层拦截，不会误判放弃")
    print("\n✅ 意图分类全部通过")


if __name__ == "__main__":
    main()
