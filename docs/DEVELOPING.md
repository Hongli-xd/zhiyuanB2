# 开发指南：添加工具与技能

本项目按「**可插拔 + 自动发现**」设计。加一个新能力 = 新建一个文件，
**不需要改注册表、不需要改 pipeline、不需要改 main**。

## 核心概念

| 概念 | 是什么 | 用什么写 |
|---|---|---|
| **Tool** | 一次原子操作（通常一个 HTTP RPC） | `@tool` 装饰一个 async 函数 |
| **Skill** | 多步编排 / 带状态的复杂能力 | `@skill` 装饰一个类（实现 `run()`） |

两者对 LLM、注册表、执行器**暴露同一个协议**，调用方无需区分。
先写函数；等它复杂到需要状态了，再升级成类，对外接口不变。

## 加一个 Tool（最常见）

在 `capabilities/<域>/` 下新建一个 `.py` 文件：

```python
from core.capability import tool, Concurrency
from core.http import get_client
from core.result import ToolResult

@tool(
    name="my_tool",                    # LLM 调用名
    description="一句话告诉 LLM 这个工具干什么、何时用",
    properties={                       # JSON-Schema 参数
        "level": {"type": "integer", "minimum": 0, "maximum": 10},
    },
    required=["level"],
    concurrency=Concurrency.CONCURRENT,  # 见下方并发策略
)
async def my_tool(level: int) -> ToolResult:
    res = await get_client().post("http://.../SomeRpc", {"level": level})
    if res.ok:
        return ToolResult.success(f"已设置为 {level}")
    return ToolResult.fail(f"设置失败({res.status})")
```

保存即生效。启动时 `core.registry.discover()` 会自动扫描到它。

## 加一个 Skill（多步 / 带状态）

```python
from core.capability import skill, Concurrency
from core.result import ToolResult

@skill(
    name="my_skill",
    description="...",
    properties={"target": {"type": "string"}},
    required=["target"],
    concurrency=Concurrency.EXCLUSIVE,
)
class MySkill:
    def __init__(self):
        ...  # 编译 LangGraph / 准备状态，只跑一次

    async def run(self, target: str, **_ignore) -> ToolResult:
        # 内部可调用多个原子工具、用 LangGraph 编排分支
        return ToolResult.success("完成")
```

## 并发策略（对物理机器人很重要）

一轮对话里 LLM 可能返回多个 tool_call。执行器按每个能力声明的策略协调：

- `SERIAL`（默认）：与其它工具串行，最稳。
- `CONCURRENT`：无害动作（灯带、闲聊），可与其它 CONCURRENT 工具并发，更快。
- `EXCLUSIVE`：危险物理动作（运动、任务启动），执行期间持有全局独占锁，
  绝不与任何工具同时跑；并自动走置信度安全门。

**判断原则**：会让机器人物理移动的 → `EXCLUSIVE`；纯展示/查询 → `CONCURRENT`；
拿不准 → 留默认 `SERIAL`。

## 统一返回：ToolResult

所有能力返回 `ToolResult(ok, message, data)`：
- `message` 会被注入对话上下文，供 LLM 生成回复并 TTS 播报（失败时务必写清原因）。
- `data` 放结构化附加信息（调试 / 链式用）。
- 旧代码返回 dict / bool 也能被 `ToolResult.coerce` 自动兼容。

## 目录结构

```
core/                 地基层（永不随工具增长）
  capability.py       Capability 协议 + @tool/@skill 装饰器 + 并发枚举
  registry.py         自动发现：扫描 capabilities/ 生成 LLM schema
  dispatcher.py       统一执行：日志/安全门/异常兜底/并发协调
  result.py           ToolResult 统一返回
  http.py             单一异步 RPC 客户端（yarl encoded=True 解决 URL 编码）

capabilities/         能力层（加东西只动这里，按域分子包）
  motion/   move.py, motion_preset.py, _motion_data.py
  display/  light.py
  task/     task_engine.py, launch_task.py

pipeline/
  audio/    ros_source.py（ROS订阅）, vad_buffer.py（纯VAD状态机,可单测）
  tts_output.py
  build.py            组装 pipeline（接 registry/dispatcher，加工具无需改）

services/  asr/（可插拔 ASR）, tts.py

tests/     test_offline.py（mock RPC 全链路）, test_vad.py（VAD 单测）
```

## 测试

```bash
python -m tests.test_offline   # 全链路（mock RPC，无需机器人）
python -m tests.test_vad       # VAD 状态机单测
```
