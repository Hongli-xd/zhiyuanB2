# A2 机器人语音 Agent（Pipecat + LangGraph）

按你的技术选型文档实现的完整语音 Agent 框架。链路：

```
A2 mic (ROS2 VAD音频16k/16bit)
   │
   ▼
ROS2AudioInputProcessor ──ASR──► TranscriptionFrame
   │
   ▼
user context aggregator
   │
   ▼
AnthropicLLMService ──工具调用──► 原子Tool / LangGraph技能
   │                                   │
   │   (HTTP RPC / StateGraph)         │ 执行结果经 result_callback
   ▼                                   ▼ 回注上下文 → LLM 生成回复
A2TTSProcessor ──PlayTTS──► A2 speaker
```

## 目录结构

```
a2_agent/
├── config.py                  # 集中配置：IP/端口、关键词映射、灯带预设（密钥走环境变量）
├── main.py                    # 机器人上的入口
├── core/                      # 地基层（永不随工具数量增长）
│   ├── capability.py          #   Capability 协议 + @tool/@skill 装饰器 + 并发策略
│   ├── registry.py            #   自动发现：扫描 capabilities/ 生成 LLM schema
│   ├── dispatcher.py          #   统一执行：日志/安全门/异常兜底/并发协调
│   ├── result.py              #   ToolResult 统一返回协议
│   └── http.py                #   单一异步 RPC 客户端（解决 URL 编码，不再用 requests/curl）
├── capabilities/              # 能力层（加工具只动这里，按域分子包）
│   ├── motion/                #   move, motion_preset（+133动作数据表）
│   ├── display/               #   light（灯带状态）
│   └── task/                  #   task_engine（原子步骤）+ launch_task（LangGraph 技能）
├── pipeline/
│   ├── audio/
│   │   ├── ros_source.py        #   ROS2 订阅 → 解 protobuf → ASR（仅音频管道职责）
│   │   ├── session_state.py     #   纯逻辑三态会话机（SLEEPING/LISTENING/PROCESSING，可单测）
│   │   ├── session_processor.py #   pipecat processor：帧驱动状态 + 独立空闲计时器 + 灯带
│   │   └── vad_buffer.py        #   纯 VAD 状态机（可单测）
│   ├── tts_output.py          #   文本帧 → A2 PlayTTS，处理打断
│   └── build.py               #   组装 pipeline（接 registry/dispatcher，加工具无需改）
├── services/
│   ├── asr/                   #   可插拔 ASR（openai / whisper / funasr）
│   └── tts.py                 #   A2 PlayTTS / StopTTS 封装
├── tests/                     # 离线测试（mock RPC 全链路 + VAD 单测）
└── docs/
    ├── DEVELOPING.md          # ★ 如何添加新工具/技能
    └── issues.md              # 历史问题与解决记录
```

> **加一个新工具/技能** = 在 `capabilities/<域>/` 下新建一个文件，写一个
> `@tool` 函数或 `@skill` 类，**不碰其它任何文件**。详见 `docs/DEVELOPING.md`。



## 会话状态管理（SessionStateProcessor）

会话的唤醒 / 连续对话 / 空闲休眠 / 灯带提示，由一个 **pipecat processor** 统一管理，
而不是散落在音频处理器里的标志位。它消费流水线里的帧来驱动一个三态状态机。

### 三个状态

| 状态 | 含义 | 音频帧 | 灯带 |
|---|---|---|---|
| `SLEEPING` | 未唤醒（初始 / 超时后） | 丢弃 | off |
| `LISTENING` | 已唤醒，等用户说话，空闲计时进行中 | 正常处理 | waiting（紫红） |
| `PROCESSING` | ASR 出文字后，正在跑 LLM+工具+TTS | 仅用于打断 | working（蓝） |

### 状态转换（由 pipecat 帧驱动）

```
                  唤醒(ROS)
   SLEEPING ───────────────► LISTENING ──Transcription──► PROCESSING
      ▲                         │  ▲                          │
      │  空闲计时器到点(60s)      │  └──BotStoppedSpeaking───────┘
      └─────────────────────────┘     (一轮播完,回 LISTENING)
                                    StartInterruption(打断)亦回 LISTENING
```

帧 → 事件映射（见 `pipeline/audio/session_processor.py`）：

| pipecat 帧 | 触发 | 效果 |
|---|---|---|
| 唤醒（ROS 线程 `notify_wakeup()`） | `on_wakeup` | → LISTENING，启动空闲计时器，首次唤醒播「我在呢」 |
| `UserStartedSpeakingFrame` | `on_speech_activity` | 重置空闲计时器（有人在说话） |
| `TranscriptionFrame` | `on_transcription` | → PROCESSING，**取消**空闲计时器 |
| `BotStoppedSpeakingFrame` | `on_turn_complete` | → LISTENING，**重启**空闲计时器（连续多轮） |
| `StartInterruptionFrame` | `on_interrupt` | → LISTENING（打断当前回复） |

用 `BotStoppedSpeakingFrame`（机器人实际播完）而非 LLM 文本结束作为「一轮结束」信号，
避免用户还没听完就重置。

### 关键设计：空闲超时用独立计时器，不靠音频帧

A2 是 VAD 驱动——**没人说话时麦克风不发帧，只有唤醒后才返回说话结果**。
所以「该超时」恰恰发生在「没有帧」的时候。原实现把空闲检查挂在音频帧回调里，
导致超时永远不触发、或只在下次开口的第一帧触发并把那句吞掉。

新实现用一个**独立的 `asyncio` 计时任务**（`pipeline/audio/session_processor.py`
的 `_idle_countdown`）：进入 LISTENING 时 `create_task(sleep(60))`，有语音活动就
`cancel()` 重起，进入 PROCESSING 就取消。计时器在事件循环里独立跑，**完全不依赖
音频帧**，没人说话也能准时触发回 SLEEPING。

### 三种场景的行为（你问过的）

- **唤醒后立刻说话**：SLEEPING→LISTENING→PROCESSING→（播完）LISTENING，正常多轮。
- **唤醒后过一段时间（>60s）再说话**：空闲计时器已把状态切回 SLEEPING，灯带转 off；
  此时说话会被音频门控（`should_process_audio()` 为 False）丢弃，需重新唤醒。
- **第二天不唤醒直接说话**：仍是 SLEEPING，麦克风本就不发帧；即便发帧也被门控丢弃。
  必须先唤醒。唤醒后立即恢复正常。

### 职责拆分

- `pipeline/audio/session_state.py` — **纯逻辑**三态机，不依赖 pipecat/ROS，可单测。
  计时器只发「arm/cancel」决策，真正的定时器由 processor 注入。
- `pipeline/audio/session_processor.py` — pipecat processor，消费帧、管真实 asyncio
  计时器、按状态切灯带、跨线程接收 ROS 唤醒。
- `pipeline/audio/ros_source.py` — 回归单一职责（音频管道）：收 ROS 消息、解 protobuf、
  问 `session.should_process_audio()` 是否处理、唤醒时 `notify_wakeup()`。

可配置：`config.IDLE_TIMEOUT`（环境变量 `A2_IDLE_TIMEOUT`，默认 60 秒）。


## 知识库（RAG，本地嵌入 + FAISS）

文本知识库以 **function calling** 形式接入：做成一个工具 `search_knowledge_base`，
由 LLM 自主决定何时调用，而不是把文档常驻塞进 system prompt。

### 为什么这样接入 LLM 才合理

- **按需检索，用完即走**：只有 LLM 判断「这问题需要本地特定知识」时才查，检索结果
  作为 tool_result 临时进入上下文，不长期占用、不稀释指令。
- **天然防幻觉**：工具描述里要求「只依据检索内容回答，查不到就说不知道」。
- **零侵入**：和现有 `@tool` 体系一致，加它就是新建一个文件,自动被发现注册。

### 什么时候走知识库，什么时候走纯 LLM

由 LLM 依据工具描述自主路由（无需额外分类器）：
- **走知识库**：本场馆/本机构特定事实——展品介绍、营业时间、流程规定、位置导引、
  专有名词等「只在你的文档里、LLM 训练数据没有」的内容。
- **走纯 LLM**：通用常识、闲聊、天气、笑话等。
- 两者不互斥：可先查知识库拿事实，再用闲聊口吻包装着说出来。

### 实现

```
capabilities/knowledge/
  _vectorstore.py   # FAISS(IndexFlatIP=余弦) + 本地 sentence-transformers 嵌入，延迟加载
  search_kb.py      # @tool search_knowledge_base：检索 top-k、按相似度阈值过滤、拼来源
  ingest.py         # 离线灌库：python -m capabilities.knowledge.ingest <文档目录>
```

本地嵌入 + FAISS，**断网可用**；索引构建一次落盘，运行时只读加载。
配置见 config：`KB_INDEX_DIR / KB_EMBED_MODEL / KB_TOP_K / KB_MIN_SCORE / KB_CHUNK_CHARS`。

## 任务中断 / 暂停 / 恢复

支持「执行任务中被唤醒打断 → 回答问题 → 询问是否继续 → 恢复或放弃」的完整交互。
基于 A2 任务引擎的真实接口 `CtrlTaskState`（Type_PAUSE/RESUME/STOP）+ `GetTask` 状态查询。

### 交互流程

```
执行任务中 ──唤醒词──► 暂停任务(CtrlTaskState PAUSE，A2 在机器人侧保存进度)
                         ↓ 轮询 GetTask 确认到 PAUSED
                      回答用户问题
                         ↓ 一轮答完
                      追问「要继续刚才的XX任务吗？」
                         ↓ 用户回复 → 意图三分类
         ┌───────────────┼─────────────────────────┐
       RESUME          ABANDON                    OTHER
    恢复任务          终止任务         回答这个问题 → 再次追问(循环)
   (CtrlTaskState    (CtrlTaskState   「先不用/等会儿」属此类，任务保持挂起
    RESUME→RUNNING)   STOP)
```

### 关键设计

- **状态保存靠 A2，不靠 Agent**：PAUSE 由任务引擎在机器人侧冻结进度，恢复用 RESUME
  从断点继续。Agent 只在内存记住「挂起了哪个 task_id」（不持久化）。
- **异步操作轮询确认**：PAUSE/RESUME 有 PAUSING/RESUMING 中间态，调用后轮询 GetTask
  直到到达 PAUSED/RUNNING 终态（或失败态/超时）才算成功，不只信 RPC 返回。
- **「先不用」≠ 放弃**：意图分类先用关键词层把「先不用/等会儿/稍后」强制归为 OTHER，
  挡在 ABANDON 判断之前；拿不准一律 OTHER（保守，宁可多问一次不误杀任务）。
  关键词判不了再交给 LLM 兜底。
- **沉默超时自动放弃**：追问后持续沉默超过 `TASK_RESUME_TIMEOUT`（默认 7 分钟）
  自动 STOP 放弃任务，避免任务无限挂起。

### 实现

```
core/task_context.py                 # SuspendedTask：进程内挂起任务容器
capabilities/task/
  task_query.py                      # GetTask/GetAllTasks：任务状态真值来源
  task_control.py                    # CtrlTaskState pause/resume/stop + 轮询确认
  intent.py                          # 意图三分类 RESUME/ABANDON/OTHER（关键词+LLM）
pipeline/audio/
  task_interrupt.py                  # 中断生命周期管理（追问/三分类路由/超时放弃）
  session_processor.py               # 接入：AWAITING 时拦截转写做路由，答完触发追问
```

## Tool 与 Skill 的落地（对应你的需求）

**需求 2 —— 把上传的 `voice_task.py` 变成一个技能：**
原脚本「关键词命中 → 切Auto → 设当前任务 → 启动任务」被拆解为：
- 3 个原子 **Tool**（`tools/task_engine.py`）：每个对应一个 HTTP RPC。
- 1 个 **Skill**（`skills/launch_task.py`）：用 LangGraph `StateGraph`
  把三步串成带「失败即中止」分支的状态机，对 LLM 暴露为单个工具
  `launch_aimmaster_task(task_id | keyword)`。关键词映射沿用原脚本的
  `KEYWORD_TASK_MAP`。

  原脚本是「ROS唤醒词 → 直接触发」；这里改成「ASR转写 → LLM理解 →
  调用技能」，更灵活（用户不必念固定关键词，LLM 会判断意图）。
  你也可以保留原来的唤醒词直触发——把 `KEYWORD_TASK_MAP` 留着即可。

**需求 3 —— 等待控制作为一个工具：**
`tools/light.py` 的 `set_status_light` 直接封装你给的灯带 curl
（`HalRgbLightService/SetRgbLightCommand`），`waiting` 预设就是你示例里的
紫红色 `{red:180,green:0,blue:100,effect:2,control:1}`。另外提供
`wait_for_person` 工具表达「进入等待状态」。

**需求 4 —— ASR / TTS：**
- TTS 用 A2 自带 `TTSService/PlayTTS`（文档 7.5），打断用 `StopTTS`。
- ASR 可插拔：`services/asr.py`。⚠️ 你提到的「pasted 文件里的 api-key」
  **没有出现在上传目录**（uploads 为空），所以我做成了可配置：在
  `config.py` 填 `ASR_API_KEY/ASR_BASE_URL`（OpenAI 兼容），或设
  `ASR_PROVIDER=whisper` 走本地 faster-whisper。把那份文件发我，
  我替换成对应厂商的原生调用。

## 配置

编辑 `config.py` 或用环境变量：

```bash
export LLM_API_KEY=sk-...            # Anthropic key
export ASR_API_KEY=...               # 你的 ASR key
export ASR_BASE_URL=...              # OpenAI 兼容网关地址
export A2_HOST=192.168.100.110       # 主控（TTS/任务/系统）
export A2_LIGHT_HOST=192.168.100.100 # 灯带服务
```

## 运行

离线验证逻辑（不需要机器人 / ROS）：
```bash
pip install -r requirements.txt
python -m test_offline
```

机器人上运行：
```bash
pip install prebuilt/a2_aimdk-2.0.1-py3-none-any.whl
source prebuilt/ros2_plugin_proto_aarch64/share/ros2_plugin_proto/local_setup.bash
python -m main
```

## 扩展：再加一个 A2 能力

1. 在 `tools/` 写一个原子 Tool（一个 HTTP RPC）。
2. 在 `tools/registry.py` 加 `FunctionSchema` + handler + `register_function`。
3. 多步业务逻辑 → 在 `skills/` 写一个 LangGraph `StateGraph` 技能，
   handler 里调它。

## 安全兜底

`ConfidenceGate`（`services/a2_client.py`）对会触发物理移动的工具
（如 `launch_aimmaster_task`）做置信度检查，低于阈值拦截。阈值/开关在
`ConfidenceGate(min_confidence=...)`。长时任务（导航）建议注册时设
`cancel_on_interruption=False`，让执行期间仍可对话。
