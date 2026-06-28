"""
集中配置区 —— 部署时只需改这一份文件。

涵盖：A2 各服务的 IP/端口、ASR 凭据、关键词→任务映射、灯带预设等。
所有可调参数都用环境变量兜底，方便容器化部署时覆盖。
"""

import os


# ──────────────────────────────────────────────────────────────────────────
#  A2 机器人各服务地址
#  说明：不同服务监听的端口不同（来自官方文档 / 你上传的脚本）。
#       TTS 在 59301；灯带在 52893；任务引擎与系统状态在 110 这台机的对应端口。
# ──────────────────────────────────────────────────────────────────────────
A2_HOST = os.getenv("A2_HOST", "192.168.100.110")        # 主控（TTS / 任务 / 系统）
A2_LIGHT_HOST = os.getenv("A2_LIGHT_HOST", "192.168.100.100")  # 灯带服务所在 IP

# TTS / 音频播放（文档 7.5）
TTS_BASE = f"http://{A2_HOST}:59301/rpc/aimdk.protocol.TTSService"

# 任务引擎 / 系统状态（来自你上传的 voice_task.py）
TASK_ENGINE_BASE = f"http://{A2_HOST}:57881/rpc/aimdk.protocol.TaskEngineService"
SYSTEM_SERVICE_BASE = f"http://{A2_HOST}:51056/rpc/aimdk.protocol.SystemService"

# 灯带控制（来自你给的 curl，端口 52893）
LIGHT_BASE = f"http://{A2_LIGHT_HOST}:52893/rpc/aimdk.protocol.HalRgbLightService"

# 运动控制（ locomote velocity channel，端口 56322）
MOTION_BASE = f"http://{A2_LIGHT_HOST}:56322/channel/%2Fmotion%2Fcontrol%2Flocomotion_velocity/pb%3Aaimdk.protocol.McLocomotionVelocityChannel"

HTTP_HEADERS = {"content-type": "application/json"}
HTTP_TIMEOUT = float(os.getenv("A2_HTTP_TIMEOUT", "5"))   # 秒

# ──────────────────────────────────────────────────────────────────────────
#  ROS2 音频输入（来自技术选型文档）
#  16kHz / 16bit PCM，带 VAD（BEGIN / PROCESSING / END）
# ──────────────────────────────────────────────────────────────────────────
AUDIO_TOPIC = os.getenv("A2_AUDIO_TOPIC", "/agent/process_audio_output/pb_3Aaimdk_2Eprotocol_2EProcessedAudioOutput")
AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1

# ──────────────────────────────────────────────────────────────────────────
#  ASR 配置
#  ⚠️ 你提到的「pasted 文件中的 api-key」我在上传目录里没有找到（uploads 为空）。
#     这里做成可插拔：默认走 OpenAI 兼容接口，也支持本地 faster-whisper。
#     部署时把 ASR_API_KEY / ASR_BASE_URL 填上即可，或切换 ASR_PROVIDER=whisper。
# ──────────────────────────────────────────────────────────────────────────
ASR_PROVIDER = os.getenv("ASR_PROVIDER", "funasr")        # "openai" | "whisper" | "funasr"
# ⚠️ 密钥务必通过环境变量注入，不要写进代码（否则会进 git 历史）。
ASR_API_KEY = os.getenv("ASR_API_KEY", "sk-e19c26823f0346b1acbc2071705bcb0f")
# ASR_BASE_URL = os.getenv("ASR_BASE_URL", "https://api.openai.com/v1")
# ASR_MODEL = os.getenv("ASR_MODEL", "whisper-1")
ASR_LANGUAGE = os.getenv("ASR_LANGUAGE", "zh")
# # 本地 faster-whisper（断网可用，ORIN 上跑 small/base 约 300–600ms）
# WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "small")
# WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")      # ORIN 有 GPU
# WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "int8_float16")

# FunASR（阿里云，ASR_PROVIDER=funasr 时启用）
FUNASR_MODEL = os.getenv("FUNASR_MODEL", "fun-asr-realtime-2026-02-28")
FUNASR_LANGUAGE_HINTS = os.getenv("FUNASR_LANGUAGE_HINTS", "zh,en")

# ──────────────────────────────────────────────────────────────────────────
#  LLM 配置
# ──────────────────────────────────────────────────────────────────────────
# ⚠️ 密钥务必通过环境变量注入，不要写进代码。
LLM_API_KEY = os.getenv("LLM_API_KEY", "sk-cp-HT93UTSKgpBYKYbEhTrU2JlSVEFO_6SEXNjQYTYCGZXwhPRCFn9WBt2NEGU9ZIBu7nJqKK3c1oCgBEPH_P7xBM0b9RoedCYi1wq4Q-cUjMYG-gD0NUSH97U")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.minimaxi.com/anthropic")
LLM_MODEL = os.getenv("LLM_MODEL", "MiniMax-M3")

# ──────────────────────────────────────────────────────────────────────────
#  TTS 播报默认参数（文档 7.5.1）
# ──────────────────────────────────────────────────────────────────────────
TTS_PRIORITY = "INTERACTION_L6"
TTS_DOMAIN = "voice_agent"

# ──────────────────────────────────────────────────────────────────────────
#  任务 ID → 人类可读名称（LLM 决策与播报用）
# ──────────────────────────────────────────────────────────────────────────
TASK_NAMES = {
    "1": "英文讲解任务：英文介绍、英文讲解场景",
    "2": "电梯等人任务：电梯、接人、接待场景",
    "6": "讲解任务：介绍、讲解场景",
}


# ──────────────────────────────────────────────────────────────────────────
#  灯带预设（来自你给的 curl）
#  effect / control 的取值沿用你示例中的值。
# ──────────────────────────────────────────────────────────────────────────
LIGHT_PRESETS = {
    # 等待中：紫红常亮呼吸（你示例里的那组颜色）
    "waiting": {"red": 180, "green": 0, "blue": 100, "effect": 2, "control": 1},
    # 工作中：蓝色
    "working": {"red": 0, "green": 80, "blue": 220, "effect": 2, "control": 1},
    # 完成：绿色
    "done": {"red": 0, "green": 200, "blue": 0, "effect": 1, "control": 1},
    # 关闭灯带
    "off": {"red": 0, "green": 0, "blue": 0, "effect": 0, "control": 0},
}


# ──────────────────────────────────────────────────────────────────────────
#  会话状态机
# ──────────────────────────────────────────────────────────────────────────
# 一次唤醒后连续多轮对话；静默超过此秒数回到休眠（需重新唤醒）。
IDLE_TIMEOUT = float(os.getenv("A2_IDLE_TIMEOUT", "60"))


# ──────────────────────────────────────────────────────────────────────────
#  知识库（本地嵌入 + FAISS，断网可用）
# ──────────────────────────────────────────────────────────────────────────
KB_INDEX_DIR = os.getenv("A2_KB_DIR", "/agibot/data/home/agi/a2_kb_index")
KB_EMBED_MODEL = os.getenv("A2_KB_EMBED_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")
KB_TOP_K = int(os.getenv("A2_KB_TOP_K", "3"))
KB_MIN_SCORE = float(os.getenv("A2_KB_MIN_SCORE", "0.3"))  # 余弦相似度阈值
KB_CHUNK_CHARS = int(os.getenv("A2_KB_CHUNK_CHARS", "300"))


# ──────────────────────────────────────────────────────────────────────────
#  任务中断 / 恢复
# ──────────────────────────────────────────────────────────────────────────
# 任务挂起后，追问「要继续吗」；用户持续沉默超过此秒数则自动放弃(STOP)任务。
TASK_RESUME_TIMEOUT = float(os.getenv("A2_TASK_RESUME_TIMEOUT", "420"))  # 7 分钟
