"""
能力注册表 —— 自动发现。

这是「永不增长」的文件：加新工具不需要改这里。
启动时它递归扫描 capabilities/ 包下所有模块，触发其中的 @tool / @skill 装饰器
自动登记到全局注册表，再统一生成 pipecat 需要的 ToolsSchema。

加一个工具的完整流程因此变成：
  1. 在 capabilities/<域>/ 下新建一个 .py 文件
  2. 写一个 @tool 函数（或 @skill 类）
  就这样。registry / dispatcher / main 一行都不用动。
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import List

from core.capability import Capability, all_capabilities

log = logging.getLogger("a2.registry")

_DISCOVERED = False


def discover(package: str = "capabilities") -> List[Capability]:
    """
    递归导入 capabilities 包下的所有子模块，触发装饰器注册。
    幂等：重复调用只会扫一次。
    """
    global _DISCOVERED
    if _DISCOVERED:
        return all_capabilities()

    pkg = importlib.import_module(package)
    count = 0
    for _finder, modname, _ispkg in pkgutil.walk_packages(pkg.__path__, prefix=f"{package}."):
        try:
            importlib.import_module(modname)
            count += 1
        except Exception as e:
            log.error("加载能力模块 %s 失败: %s", modname, e)

    _DISCOVERED = True
    caps = all_capabilities()
    log.info("能力自动发现完成：扫描 %d 个模块，注册 %d 个能力 -> %s",
             count, len(caps), [c.name for c in caps])
    return caps


def build_tools_schema():
    """把已注册的能力转成 pipecat 的 ToolsSchema 交给 LLM。"""
    from pipecat.adapters.schemas.function_schema import FunctionSchema
    from pipecat.adapters.schemas.tools_schema import ToolsSchema
    discover()
    schemas = []
    for cap in all_capabilities():
        schemas.append(FunctionSchema(
            name=cap.name,
            description=cap.description,
            properties=cap.parameters.get("properties", {}),
            required=cap.parameters.get("required", []),
        ))
    return ToolsSchema(standard_tools=schemas)
