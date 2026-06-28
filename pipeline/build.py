"""
组装 Pipecat pipeline。

数据流：ROS音频 → ASR → user上下文 → LLM(+工具) → A2 TTS → assistant上下文

工具/技能不再手写注册：core.registry 自动发现 capabilities/ 下所有 @tool/@skill，
core.dispatcher 统一注册并套上日志/安全门/并发策略。加新工具无需改本文件。
"""

import logging

from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.services.anthropic.llm import AnthropicLLMService

import config
from core.dispatcher import register_to_llm
from core.registry import build_tools_schema
from pipeline.tts_output import A2TTSProcessor
from pipeline.audio.session_processor import SessionStateProcessor

log = logging.getLogger("a2.pipeline")


SYSTEM_PROMPT = """你是智元 A2 机器人的语音交互助手。你以完全接管模式运行，全权负责理解用户语音并执行机器人动作。

## 对话风格
- 根据用户意图自行判断：执行任务 还是 日常闲聊。
- 日常闲聊时，你是博学的百科助手，说话有节奏、有语气助词（嗯、嘛、哦、呀），可以适当幽默，但不过度。
- 执行任务时，用简短口语化的指令确认，不要 markdown、列表或表情符号。
- 所有回复都会被 TTS 念出来，每句控制在 20 字以内，语气自然。

## 工具调用规则
每当决定调用工具时，回复必须同时满足：
  1. 先有一句对用户的口头回应（如"好的，我去接人"、"嗯，我帮你打开灯"）
  2. 紧随其后才是 tool_call 格式
  不要只发 tool_call 而不说话。
- 讲解/电梯等人等任务 → launch_aimmaster_task（直接给 task_id）。
- 需要状态指示时 → set_status_light。
- 物理移动指令：确认意图后再执行。
- 工具执行完成后，根据返回结果一句话告知用户；ok=False 时必须说明失败原因。

## 知识库
- 当用户问到本场馆/本机构特定的事实（展品介绍、营业时间、流程规定、位置导引、
  专有名词等，属于你通用知识里没有、只在本地文档里的内容）时，调用
  search_knowledge_base 查询，再用口语化方式把答案说出来。
- 只依据检索返回的内容回答，不要编造；查不到就如实说不知道。
- 通用常识、闲聊、天气等不要调用知识库，直接回答。

## 拒接/不会的情况
如果用户的要求你确实做不到，先口头说一句"抱歉，这个我还不会"，再拒绝，不要空缺。"""


def build_llm():
    llm = AnthropicLLMService(
        api_key=config.LLM_API_KEY,
        settings=AnthropicLLMService.Settings(model=config.LLM_MODEL),
    )
    # Monkey-patch：强制使用 config 的 base_url（pipecat 0.0.108 对兼容端点的已知问题）
    llm._client.base_url = config.LLM_BASE_URL.rstrip("/")
    register_to_llm(llm)  # 自动发现并注册所有能力
    return llm


def build_context() -> LLMContext:
    ctx = LLMContext()
    ctx.set_messages([{"role": "system", "content": SYSTEM_PROMPT}])
    ctx.set_tools(build_tools_schema())  # 自动生成的工具 schema
    return ctx


def build_pipeline(audio_input_factory):
    """
    audio_input_factory: 接收 session、返回音频源 processor 的工厂。
      - 机器人上：lambda s: ROS2AudioInputProcessor(s)
      - 测试：lambda s: MockTextInput(...)
    会话状态(唤醒/空闲/灯带)由 SessionStateProcessor 统一管理。
    """
    llm = build_llm()
    context = build_context()
    aggregators = LLMContextAggregatorPair(context)
    tts_out = A2TTSProcessor(session=session)

    session = SessionStateProcessor(idle_timeout=config.IDLE_TIMEOUT)
    audio_input = audio_input_factory(session)

    pipeline = Pipeline([
        audio_input,            # 音频源：产出 Transcription / Speaking 帧
        session,                # 会话状态机：消费帧驱动 SLEEPING/LISTENING/PROCESSING + 灯带
        aggregators.user(),
        llm,
        tts_out,
        aggregators.assistant(),
    ])
    return pipeline, llm, session
