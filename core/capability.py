"""
能力（Capability）统一协议 —— 整个可插拔体系的核心。

设计目标：
  - 加一个 tool = 写一个被 @tool 装饰的 async 函数，不碰任何其它文件。
  - 复杂的多步 skill = 写一个被 @skill 装饰、实现 run() 的类。
  - 函数和类对注册表 / dispatcher / LLM 暴露同一个协议，调用方无需区分。

一个 Capability 对外暴露：
  name            工具名（LLM 调用时用）
  description     给 LLM 的说明
  parameters      JSON-Schema 形式的参数定义（properties + required）
  concurrency     并发策略（默认 SERIAL：安全优先；显式 CONCURRENT 才放开并发）
  run(**kwargs)   执行，返回 ToolResult

并发策略的意义（对物理机器人很重要）：
  一轮对话里 LLM 可能返回多个 tool_call。dispatcher 默认串行执行（最稳），
  只有显式标注 CONCURRENT 的无害工具（灯带、闲聊类）才会被并发调度。
  EXCLUSIVE 用于运动类等「绝不能与任何东西同时跑」的危险动作。
"""

from __future__ import annotations

import enum
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from core.result import ToolResult

log = logging.getLogger("a2.capability")


class Concurrency(enum.Enum):
    SERIAL = "serial"          # 默认：与其它工具串行执行（安全）
    CONCURRENT = "concurrent"  # 可与其它 CONCURRENT 工具并发（灯带、闲聊等无害动作）
    EXCLUSIVE = "exclusive"    # 独占：执行期间不允许任何其它工具同时跑（运动等危险动作）


@dataclass
class Capability:
    """一个对 LLM 暴露的能力（tool 或 skill 统一表示）。"""

    name: str
    description: str
    parameters: Dict[str, Any]            # {"type":"object","properties":{...},"required":[...]}
    handler: Callable[..., Awaitable[Any]]
    concurrency: Concurrency = Concurrency.SERIAL
    required: List[str] = field(default_factory=list)

    async def run(self, **kwargs: Any) -> ToolResult:
        """统一执行入口：调用底层 handler 并把返回值规整为 ToolResult。"""
        raw = await self.handler(**kwargs)
        return ToolResult.coerce(raw)


# ── 全局注册表（装饰器在 import 时往这里登记）─────────────────────────────────
_REGISTRY: Dict[str, Capability] = {}


def _register(cap: Capability) -> None:
    if cap.name in _REGISTRY:
        log.warning("能力 %s 重复注册，后者覆盖前者", cap.name)
    _REGISTRY[cap.name] = cap
    log.debug("已登记能力: %s (%s)", cap.name, cap.concurrency.value)


def all_capabilities() -> List[Capability]:
    return list(_REGISTRY.values())


def get_capability(name: str) -> Optional[Capability]:
    return _REGISTRY.get(name)


def clear_registry() -> None:
    """仅供测试使用。"""
    _REGISTRY.clear()


# ── 装饰器 1：@tool —— 把一个 async 函数变成能力 ──────────────────────────────
def tool(
    name: str,
    description: str,
    properties: Optional[Dict[str, Any]] = None,
    required: Optional[List[str]] = None,
    concurrency: Concurrency = Concurrency.SERIAL,
):
    """
    用法：
        @tool(
            name="set_status_light",
            description="设置灯带状态",
            properties={"preset": {"type": "string", "enum": [...]}},
            concurrency=Concurrency.CONCURRENT,
        )
        async def set_status_light(preset=None, ...) -> ToolResult:
            ...
    """
    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"@tool 要求 async 函数: {fn.__name__}")
        cap = Capability(
            name=name,
            description=description,
            parameters={
                "type": "object",
                "properties": properties or {},
                "required": required or [],
            },
            handler=fn,
            concurrency=concurrency,
            required=required or [],
        )
        _register(cap)
        fn._capability = cap  # 便于测试 / 反查
        return fn

    return decorator


# ── 装饰器 2：@skill —— 把一个类变成能力 ─────────────────────────────────────
def skill(
    name: str,
    description: str,
    properties: Optional[Dict[str, Any]] = None,
    required: Optional[List[str]] = None,
    concurrency: Concurrency = Concurrency.SERIAL,
):
    """
    用法（适合带状态 / 多步编排的复杂能力）：
        @skill(name="launch_aimmaster_task", description="...", properties={...})
        class LaunchTaskSkill:
            async def run(self, task_id, **kwargs) -> ToolResult:
                ...

    类会被实例化一次（单例），其 run() 作为 handler。
    """
    def decorator(cls: type) -> type:
        if not hasattr(cls, "run"):
            raise TypeError(f"@skill 要求类实现 run() 方法: {cls.__name__}")
        instance = cls()
        cap = Capability(
            name=name,
            description=description,
            parameters={
                "type": "object",
                "properties": properties or {},
                "required": required or [],
            },
            handler=instance.run,
            concurrency=concurrency,
            required=required or [],
        )
        _register(cap)
        cls._capability = cap
        return cls

    return decorator
