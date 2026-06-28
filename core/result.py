"""
统一的工具/技能返回协议。

无论是一次原子 RPC 的 Tool，还是多步编排的 Skill，对外都返回同一种结构，
这样 dispatcher 能统一处理、LLM 看到的 tool_result 格式也一致。

字段约定：
  ok       —— 是否成功（LLM 据此决定是否向用户报错）
  message  —— 一句人类可读说明，会被注入对话上下文供 LLM 生成回复 + TTS 播报
  data     —— 可选的结构化附加数据（调试 / 链式调用用，LLM 一般不需要）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class ToolResult:
    ok: bool
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转成 pipecat result_callback 需要的 dict。"""
        out: Dict[str, Any] = {"ok": self.ok, "message": self.message}
        if self.data:
            out.update(self.data)
        return out

    @classmethod
    def success(cls, message: str = "", **data: Any) -> "ToolResult":
        return cls(ok=True, message=message, data=data)

    @classmethod
    def fail(cls, message: str, **data: Any) -> "ToolResult":
        return cls(ok=False, message=message, data=data)

    @classmethod
    def coerce(cls, value: Any) -> "ToolResult":
        """
        把工具返回的任意值规整成 ToolResult。
        兼容旧工具直接返回 dict / bool / None 的情况，保证平滑过渡。
        """
        if isinstance(value, ToolResult):
            return value
        if value is None:
            return cls.success()
        if isinstance(value, bool):
            return cls(ok=value, message="" if value else "执行失败")
        if isinstance(value, dict):
            ok = bool(value.get("ok", True))
            message = value.get("message") or value.get("status") or ""
            data = {k: v for k, v in value.items() if k not in ("ok", "message")}
            return cls(ok=ok, message=message, data=data)
        # 其它类型：当作成功，值塞进 data
        return cls.success(data={"value": value})
