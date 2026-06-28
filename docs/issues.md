# 问题汇总

本文档记录 A2 语音 Agent 开发过程中遇到的所有问题及解决方案。

---

## 1. Audio Subscribe DDS 匹配失败

**症状**：ROS2 音频 topic 在唤醒后才开始发布数据，但订阅在启动时就创建了，导致收不到音频。

**根因**：embodied_agent 唤醒前不发布音频，订阅先于发布建立，DDS 发现机制无法匹配。

**修复**：将音频订阅延迟到唤醒后才创建（`pipeline/ros_audio_input.py`）。

---

## 2. Wakeup Topic QoS 不兼容

**症状**：`WARN New publisher discovered on topic '/agent/wakeup/...', offering incompatible QoS. No messages will be received from it. Last incompatible policy: DURABILITY`

**根因**：embodied_agent 发布 wakeup topic 时用 `VOLATILE` durability，但代码订阅用 `TRANSIENT_LOCAL`。

**修复**：`pipeline/ros_audio_input.py` 中 wakeup 订阅的 durability 从 `TRANSIENT_LOCAL` 改为 `VOLATILE`。

---

## 3. LLM API 403 Forbidden

**症状**：`POST https://api.anthropic.com/v1/messages 403 Forbidden`

**根因**：两个问题：
1. `LLM_MODEL` 默认值是 `MiniMax-M2.7`，但 robot 上环境变量被设成了 `claude-sonnet-4-7-20250514`（Anthropic 模型名），而 API key 是 MiniMax 的，导致 403。
2. `pipecat.Anthro picLLMService` 内部用 `yarl.URL()` 默认解码 URL，导致 URL 中的编码字符（`%2F`、`%3A`）被破坏。

**修复**：
- 确认 `LLM_MODEL` 环境变量设为 `MiniMax-M2.7` 或 `MiniMax-M3`
- `build_pipeline` 中 monkey-patch `llm._client.base_url` 指向 `https://api.minimaxi.com/anthropic`

---

## 4. LLM BASE_URL 路径重复 `/v1/v1/`

**症状**：`POST https://api.minimaxi.com/anthropic/v1/v1/messages 404 page not found`

**根因**：`config.LLM_BASE_URL` 末尾带 `/v1`（`https://api.minimaxi.com/anthropic/v1`），而 `AsyncAnthropic` 内部又会追加 `/v1/messages`，导致路径重复。

**修复**：`config.LLM_BASE_URL` 改为 `https://api.minimaxi.com/anthropic`（去掉末尾 `/v1`）。

---

## 5. Motion Channel URL 编码问题

**症状**：`POST .../pb%3Aaimdk.protocol... 404 The resource '/channel/%2Fmotion.../pb:aimdk...' was not found`

**根因**：aiohttp 对 URL 路径中的 `%2F` 和 `%3A` 先解码再重建，导致编码字符被破坏：
- `%3A` → `:` → robot 收到 `pb:aimdk` 而非 `pb%3Aaimdk`
- aiohttp 内部用 `yarl.URL()` 默认行为处理，破坏了编码

**修复**：运动控制请求改用 `asyncio.create_subprocess_exec` 调用 `curl` 原生发送，完全绕过 HTTP 库的 URL 解析。

---

## 6. Motion Stop Payload 字段名错误

**症状**：运动停止指令发出去但 robot 无反应。

**根因**：`stop_payload["data"].update(forward=0.0, ...)` — 字段名用的是 `forward` 而不是 `forward_velocity`。

**修复**：`stop_payload` 完整构造 `forward_velocity`、`lateral_velocity`、`angular_velocity` 三个字段。

---

## 7. Motion ControlSource 不一致

**症状**：curl 发 motion 能成功，代码发失败（Robot 返回 500）。

**根因**：用户 curl 用 `ControlSource_MANUAL`，代码用 `ControlSource_API`。

**修复**：统一使用 `ControlSource_MANUAL`。

---

## 8. RPC 响应判断条件错误

**症状**：`migrate_to_auto` 返回 `False`，但 robot 日志显示 `Transition to Auto success`。

**根因**：判断条件写的是 `"is_success": true`（有空格），而 robot 实际返回 `"is_success":true`（无空格）或者根本没有该字段。

**修复**：统一改成检查 `code":"0"`。

---

## 9. terminal_input 文件监控路径问题

**症状**：`TerminalTextInput` 启动后写入文件无反应。

**根因**：文件路径 `INPUT_FILE = "a2_input.txt"` 是相对路径，工作目录不确定导致找不到文件。

**修复**：使用 `../a2_input.txt`（相对 `pipeline/` 目录），或使用绝对路径 `/a2_input.txt`。

---

## 10. terminal_input push_frame 时机问题

**症状**：`StartFrame not received yet` 错误。

**根因**：`TerminalTextInput` 在 pipeline 启动前就开始推送帧，但 pipecat processor 需要先收到 StartFrame 才能处理后续帧。

**修复**：`TerminalTextInput._file_watch_loop()` 只负责监控文件，真正的帧推送在 pipeline 启动后才开始。

---

## 11. motion.py 使用 aiohttp 阻塞事件循环

**症状**：运动指令发送时 pipeline 卡住。

**根因**：`subprocess.run` 是同步阻塞调用，在 async 函数中会阻塞整个事件循环。

**修复**：改用 `asyncio.create_subprocess_exec` 异步执行 curl。

---

## 备注

### aiohttp vs requests vs curl 对 URL 编码的处理

| 库 | `%2F` 保留 | `%3A` 保留 |
|---|---|---|
| `requests` (PreparedRequest) | ✅ | ✅ |
| `aiohttp` (默认) | ✅ | ❌ 解码成 `:` |
| `asyncio.create_subprocess_exec + curl` | ✅ | ✅ |

对于含有已编码字符的 URL（motion channel），必须用 `curl` 绕过 HTTP 库的编码问题。

### 工具/技能区分

- **Tool**：单次原子操作（如 `move`、`play_motion`、`set_status_light`）
- **Skill**：多步组合逻辑（如 `launch_aimmaster_task` 用 LangGraph StateGraph 实现三步流水线）

### pipecat 0.0.108 已知问题

- `AnthropicLLMService` 不接受 `base_url` 参数构造，需 monkey-patch `_client.base_url`
- `LLMContext` API 文档可能过时，以实际代码行为为准
