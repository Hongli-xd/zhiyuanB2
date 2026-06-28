"""
统一执行器（Dispatcher）—— 所有工具调用的唯一通道。

它把原先散落在每个 handler 里的「横切关注点」收敛到一处：
  - 日志（调用前 / 结果后的统一格式）
  - 安全门（运动等危险动作的置信度检查）
  - 异常兜底（任何工具抛错都转成 ToolResult.fail，不会崩 pipeline）
  - 结果标准化（统一成 ToolResult / dict）
  - 并发策略（默认串行；CONCURRENT 工具可并发；EXCLUSIVE 独占）

因为这些只写一份，每个工具文件就只剩纯业务逻辑，加再多工具也不会把样板复制 N 遍。

与 pipecat 的对接：
  register_to_llm(llm) 把每个能力包一层统一的 handler 注册给 pipecat 的 LLM service。
  pipecat 对一轮里的多个 tool_call 会分别回调；我们在 handler 内按并发策略协调。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from core.capability import Capability, Concurrency, all_capabilities
from core.registry import discover
from core.result import ToolResult

log = logging.getLogger("a2.dispatcher")


class ConfidenceGate:
    """
    危险动作（运动 / 任务启动）执行前的置信度兜底。
    未提供置信度时按保守原则放行但记录；低于阈值则拦截。
    """

    def __init__(self, min_confidence: float = 0.6):
        self.min_confidence = min_confidence

    def check(self, confidence: Optional[float], name: str) -> tuple[bool, str]:
        if confidence is None:
            return True, ""
        if confidence < self.min_confidence:
            msg = f"指令置信度 {confidence:.2f} 低于阈值 {self.min_confidence}，已拦截"
            log.warning("[ConfidenceGate] %s: %s", name, msg)
            return False, msg
        return True, ""


# EXCLUSIVE 工具执行期间持有的全局锁：保证危险动作不与任何其它工具同时跑
_exclusive_lock = asyncio.Lock()

confidence_gate = ConfidenceGate()


async def execute(cap: Capability, arguments: dict) -> ToolResult:
    """
    执行单个能力，统一套上日志 / 安全门 / 异常兜底。
    这是所有工具调用的真正核心，pipecat handler 和并发编排都最终走到这里。
    """
    args = dict(arguments or {})
    log.info("🔧 调用 %s -> %s", cap.name, args)

    # 危险动作的置信度检查（EXCLUSIVE 类默认视为危险）
    if cap.concurrency == Concurrency.EXCLUSIVE:
        ok, msg = confidence_gate.check(args.pop("confidence", None), cap.name)
        if not ok:
            return ToolResult.fail(f"安全拦截：{msg}")
    else:
        args.pop("confidence", None)  # 非危险动作丢弃置信度参数，避免污染 handler

    try:
        if cap.concurrency == Concurrency.EXCLUSIVE:
            async with _exclusive_lock:
                result = await cap.run(**args)
        else:
            result = await cap.run(**args)
    except TypeError as e:
        log.error("工具 %s 参数不匹配: %s", cap.name, e)
        return ToolResult.fail(f"参数错误：{e}")
    except Exception as e:
        log.exception("工具 %s 执行异常", cap.name)
        return ToolResult.fail(f"{cap.name} 执行出错：{e}")

    log.info("✅ %s 结果 -> ok=%s msg=%s", cap.name, result.ok, result.message)
    return result


def register_to_llm(llm) -> None:
    """
    把所有已发现的能力注册给 pipecat 的 LLM service。
    每个能力包一层统一 handler：取参数 → execute() → result_callback。
    """
    discover()

    def _make_handler(cap: Capability):
        async def _handler(params):
            result = await execute(cap, params.arguments)
            await params.result_callback(result.to_dict())
        return _handler

    names = []
    for cap in all_capabilities():
        llm.register_function(cap.name, _make_handler(cap))
        names.append(cap.name)
    log.info("已注册 %d 个能力到 LLM: %s", len(names), names)
