"""
追问「要继续刚才的任务吗」之后，对用户回复做三分类。

三类：
  RESUME   —— 明确同意继续
  ABANDON  —— 明确放弃任务
  OTHER    —— 别的问题 / 含糊 / 推迟（"先不用""等会儿"），任务保持挂起

核心约束（用户反复强调）：
  「先不用 / 等一下 / 稍后 / 不急」是推迟，归 OTHER，绝不能当成 ABANDON。
  只有明确「放弃 / 取消 / 不做了 / 别做了」才是 ABANDON。
  拿不准一律 OTHER（保守：误判 OTHER 只多问一次，误判 ABANDON 会丢任务）。

先用关键词快速判定（零延迟、确定性强），命中不了再交给 LLM。
"""

from __future__ import annotations

import logging

log = logging.getLogger("a2.task.intent")

RESUME = "RESUME"
ABANDON = "ABANDON"
OTHER = "OTHER"

# 明确继续
_RESUME_KW = ["继续", "接着", "恢复", "go on", "接著", "继续吧", "继续执行", "开始吧"]
# 明确放弃（注意：不含"先不用"这类推迟词）
_ABANDON_KW = ["放弃", "取消", "不做了", "别做了", "不用做了", "终止", "停掉这个任务",
               "结束任务", "算了不做", "不执行了"]
# 明确推迟（强制 OTHER，即使包含"不用"等否定词也不算放弃）
_DEFER_KW = ["先不用", "先不", "等一下", "等会", "等会儿", "待会", "稍后", "不急",
             "晚点", "过会", "一会儿再", "先放着"]


def classify_keyword(text: str) -> str | None:
    """关键词快速分类。返回 RESUME/ABANDON/OTHER 或 None(无法判定,交给 LLM)。"""
    t = (text or "").strip()
    if not t:
        return OTHER
    # 推迟优先级最高：先不用类直接 OTHER，挡在 ABANDON 前面
    if any(k in t for k in _DEFER_KW):
        return OTHER
    if any(k in t for k in _ABANDON_KW):
        return ABANDON
    if any(k in t for k in _RESUME_KW):
        return RESUME
    return None


async def classify_llm(text: str, llm_call) -> str:
    """
    关键词判不了时，用 LLM 兜底。llm_call(prompt)->str 由外层注入（解耦）。
    始终返回三类之一，异常时保守归 OTHER。
    """
    prompt = (
        "判断用户这句话对「是否继续之前暂停的任务」表达了什么意图，"
        "只回一个词：RESUME / ABANDON / OTHER。\n"
        "规则：明确要继续=RESUME；明确放弃/取消/不做了=ABANDON；"
        "其它问题、含糊、或『先不用/等会儿/稍后』这类推迟=OTHER。"
        "拿不准一律 OTHER。\n"
        f"用户：{text}\n意图："
    )
    try:
        ans = (await llm_call(prompt)).strip().upper()
        for label in (RESUME, ABANDON, OTHER):
            if label in ans:
                return label
    except Exception as e:
        log.error("意图分类 LLM 调用失败: %s", e)
    return OTHER


async def classify(text: str, llm_call=None) -> str:
    """综合分类：先关键词，判不了且有 llm_call 才走 LLM，否则 OTHER。"""
    kw = classify_keyword(text)
    if kw is not None:
        return kw
    if llm_call is not None:
        return await classify_llm(text, llm_call)
    return OTHER
