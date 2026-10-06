# Agent 执行链路独立审计（2026-10-05）

本审计依据最新代码、近期提交、真实运行日志、公开 Codex 源码和本次编写的探针。结论按依据强度标注：

- **[实测]**：日志数据或探针直接复现。
- **[源码]**：通过阅读代码确认，但没有单独运行。
- **[推断]**：有证据支持，但尚未直接测量。

## 0. 范围与版本

- 代码：`15ee10e3`。它已作为 Host **1.0.27** 发布到 stable，并于 13:59Z 在本机激活。之后 main 上新增的 `23f97180`、`2f31e6a8` 只修改 Web Portal。
- 运行日志：`~/.loom/agent_runtime/sessions/<id>/events.jsonl`
  - `13d05daf…`：10-05 的浏览器验收。turn 1 时间为 06:21–06:59Z，运行 Host 1.0.23（`394af171`）。turn 2 时间为 13:28–13:39Z，运行 Host 1.0.26（`d57e156b`）。
  - `2d6e4fca…`：10-01 至 10-05 的会话，共 24 个回合、538 次模型请求，覆盖多个 Host 版本。
  - `desktop-react/electron.log.163-dev`、`.168-dev`：09-30 以来的超时日志。
- Codex：只对照公开源码 `openai/codex@a7660cd15490875b8c22f66e577da115ed927fe3`，不推测 Codex 桌面版的私有实现。
- 探针：脚本位于 `scratch/audit_probes/`，只在本地使用，运行方式见该目录的 README。脚本用伪造的 OpenAI 兼容客户端驱动真实的 `build_ai_platform → StreamingAIPlatform → ModelExecutor → AgentRuntime`。
- 测试：本地全量 pytest 与 CI 结论一致。HEAD 有 2 个真实失败（见 §3.5）。本地另有 3 个失败来自审计环境，原因是子进程找不到依赖或 PTY 环境不完整；这 3 个测试在 CI 中通过。

## 1. 总体结论

1. **有效修复：** 运行时自身导致的回合终止已经修掉，包括强制收尾审查、驳回次数上限、工具参数错误直接终止回合，以及伪造的工具旁白。§4 列出了对应证据。
2. **主要问题仍在：** 任务突然结束、流式超时、进度文字过多和证据不足这四类问题仍然存在。其中至少两个根因此前没有被识别：流式停滞判定与 MiniMax 工具参数下发方式冲突；请求前缀不稳定，导致缓存几乎失效。
3. **四条问题链互相放大：**
   - **流式超时：** 60 秒内容停滞判定，与 MiniMax 最后一次性下发工具参数的方式冲突。结果是大块写入在任何重试下都必然失败；重试只会把等待时间和 token 消耗放大 3 倍。
   - **回合边界：** 不带工具调用的回复会结束回合，这一点与 Codex 相同。但模型从未被告知这条协议，而且回合结束会销毁浏览器 session。因此，“突然结束”“session 失效”和“上下文接不上”实际上是同一个问题的三个表现。
   - **上下文与成本：** 内置 MiniMax 没有声明上下文窗口，所以永远不会自动压缩。同时，请求开头的第 2 条消息每一步都会变化，导致前缀缓存基本失效。一个 38 分钟的回合消耗 1477 万输入 token，随后触发 429。
   - **表达层：** 表情贴纸协议以“高优先级”系统消息注入每一步，运行时还会把贴纸写回模型自己的历史消息。这与主提示词“一两句话、不需要逐条回执”的要求相反。实际有 97% 的步骤带旁白；旁白占上下文的 20%，占模型生成字符的 60%。
4. **架构层层叠加：** 生产 `AgentRuntime` 的继承链有 22 层。`_prepare_model_request` 被 7 层覆盖；事件记录器 `_record` 被 5 层覆盖，并承担副作用。`app/__init__.py` 安装了 19 个导入期补丁；App Server 启动时还会替换运行时类的 6 个方法。因此，单元测试运行的代码和生产运行的代码并不相同。
5. **1.0.27 有回归，需要立即修复：** 最新提交的方向正确，即让模型知道 session 为什么失效。但它会在**所有会话、所有后续回合、每一次请求**的最前面插入“浏览器资源已释放”，即使会话从未打开过浏览器。这还会让跨回合的前缀缓存全部失效（§5）。

## 2. 七个症状：根因、修复效果与现状

### 2.1 任务突然结束

自 10-01 起，长回合的终止情况如下，也包括被模型提前结束、但状态显示为“完成”的回合：

| 时间(UTC) | 版本 | 终止原因 | 现状 |
| --- | --- | --- | --- |
| 10-03 06:40 | 未确认 | `APIConnectionError`，重试后仍失败 | 传输层失败，属于合理终止 |
| 10-04 11:23–14:43 | 1.0.11–1.0.17 | 强制收尾审查失败、3 次驳回上限、审查上下文超限 | **已修复**（`cb239cc8`、`8b00b21b`），1.0.18 后未再出现 |
| 10-05 02:26 | 1.0.18–1.0.21 | 模型回复“让我直接确认 item-C 的 clicked 状态，然后继续。”，但没有发工具调用，回合因此完成。计划 7 步全部未完成，用户随后问“做完了吗” | **未处理** |
| 10-05 05:06 | 1.0.23 之前 | 工具参数错误耗尽模型重试（该回合累计 6 次），触发 `turn_failed` | **已修复**（`394af171`） |
| 10-05 06:59 | 1.0.23 | MiniMax 返回 429 “已达到 Token Plan 用量上限 (2056)” | 外部配额问题，但根因是上下文成本（§2.4） |
| 10-05 13:39 | 1.0.26 | 连续 3 次 `stream_stall_timeout` | **未处理**（§2.7） |

已经修复的主要是运行时自身制造的终止。剩下的问题来自三方面：回合协议没有告诉模型、流式超时规则，以及上下文成本。

**[源码] 回合协议。** 如果回复没有工具调用，而且 `end_turn` 不是显式 `False`，`TurnRunner` 会进入 `legacy_finish_compatibility → DELIVER` 并结束回合（`turn_runner.py:596-627`，`execution_state.py`）。Codex 的判断方式相同：`needs_follow_up = model_needs_follow_up || has_pending_input`；如果为 false，就运行 Stop hook 并结束回合（`codex-rs/core/src/session/turn.rs:566,653`）。

差别在于提示词：

- Codex 提示词明确要求：“Persist until the task is fully handled end-to-end within the current turn”（`gpt_5_1_prompt.md:30`）。
- Loom 默认提示词 v9，以及运行时状态、语言、贴纸、计划、浏览器契约和转向说明等注入消息中，**都没有说明“不带工具调用的回复会结束回合”**。本次已全文检索确认。

对 MiniMax 这类没有按 Codex harness 训练的模型，缺少的是协议说明，而不是需要额外加一个关键词分类器。

**[实测] 输出截断。** 如果输出在工具参数中间被截断（`finish_reason=length`），`_StreamAccumulator.finalize` 会先解析 JSON，并抛出 `invalid streamed JSON arguments`（`streaming_platform.py:144`）。运行时随后把它当成格式错误：

- 发给模型的是通用提示：“empty, malformed…”；
- 专为截断设计的 `TRUNCATED_RECOVERY_INSTRUCTION` 永远不会用于工具调用；
- 3 次后回合失败；
- 事件里也不会记录 finish_reason。

探针 `probe_truncated_tool_call.py` 可以复现。生产日志 13:31:24 的 `exec` 失败是否属于这种情况，目前无法判断，因为当时没有记录 finish_reason。

### 2.2 长时间运行但进展不清晰

**[实测]** `13d05daf` turn 1 运行 38 分钟，共 156 次请求；其中模型耗时 1766 秒，占 78%。

- 计划只更新了 4 次。
- 06:28 至 06:40 的 12 分钟里，模型完成了 50 次压力动作，但计划一直停在“进行中”。
- 整个回合共产生 147 条旁白。

用户看到的进展几乎都来自旁白，而结构化计划的粒度太粗。

很多客观进度信号可以直接从事件中推导，但现在没有展示出来，例如：

- 最近一次工具结果距今多久；
- 当前阶段执行和失败了多少次；
- 当前模型请求已经等待多久；
- 流式响应现在是否仍有输出。

这些信号都不依赖模型自述。

### 2.3 进度文字过多

**[实测]** 下表统计已剔除贴纸标记：

| 回合 | 带工具调用的步骤 | 带旁白比例 | 旁白中位长度 | p90 |
| --- | --- | --- | --- | --- |
| 2d6e4fca 10-02 04:31 | 67 | 100% | 516 | 1168 |
| 2d6e4fca 10-04 14:20（审查期间） | 60 | 100% | 1334 | 2149 |
| 2d6e4fca 10-05 02:13 | 54 | 94% | 456 | 887 |
| 13d05daf 10-05 06:21 | 151 | 97% | 412 | 1030 |

提示词 v9 自 10-04 起要求“Routine tool receipts do not need an acknowledgment”，但前后数据没有明显改善。

原因按证据强度排列：

1. **[实测] 贴纸协议持续推动更多文字。** `StreamingAgentRuntime` 和 `BalancedStickerStreamingAgentRuntime` 每一步都会注入两条系统消息，约 3.1K 字符，并标注“优先级高于普通格式偏好”和“高优先级”。它们要求模型寻找“情绪承接点”，规定“正文至少给 1 个自然候选”，并“禁止只在末尾象征性给 1 个”。这些规则会推动模型写出更长、更情绪化的文字，与主提示词的方向相反。turn 1 的 151 条回复中有 145 条带贴纸，共 300 个。
2. **[实测] 运行时改写模型输出，并写回规范历史。**
   - 当正文不少于 18 个字且没有贴纸时，`ensure_balanced_sticker_coverage` 会插入贴纸（`sticker_body_runtime.py:216`）。
   - `StreamingAgentRuntime._record` 会把改写后的文本同时写入 `MODEL_RESPONSE` 事件和 `session.messages[-1]`（`streaming_runtime.py:548`）。
   - 同一个事件里却硬编码了 `runtime_authored: False`（`turn_runner.py:540`）。

   探针 `probe_history_injection.py` 中，模型返回“…没有需要修改的地方了。”，历史记录却变成“…了。[[AI_LEDGER_INLINE_STICKER:soft_smile]]”。模型下一步看到的是“我每句话都带贴纸”，这种风格会被自我强化。它与 `d1891e46`“保留原生输出”的目标直接冲突。
3. **[推断] 旁白可能在替代推理。** MiniMax-M3 在 `thinking: adaptive` 下几乎没有使用推理通道：turn 1 的 151 条回复，可见推理合计只有 278 字符。旁白实际上承担了“边想边做”的作用。如果只是要求模型少说，可能会降低决策质量。更好的方向是把思考放到推理通道或折叠区，而不是直接删除。
4. 用户自己的验收提示也写了“可以发送进度说明”。

这些旁白会带来实际代价：

- 截至 `13d05daf` 结束，上下文共 345 条消息，其中助手可见文字占 20.6%，约 8.7 万字符，每一步都会被重新发送。
- turn 1 的生成字符中，旁白约 8.4 万，占 60%；工具参数约 5.6 万，占 40%。

### 2.4 上下文衔接不可靠，成本过高

**[实测] 窗口未知，所以永远不会自动压缩。**

- 内置 `builtin:minimax` 的请求中，`context_limits.window_known=false`，`context_budget_source=unbounded`。
- `context_budget.py:760` 只有在 `window_known` 为真时才触发自动压缩。
- turn 1 的上下文从 15.8K token 增长到 152K。
- turn 2 中用户只说了“请继续吧”，但第一步就已经是 153K，最后增长到 175K。
- turn 1 共消耗 14,771,630 个输入 token 和 63,411 个输出 token，之后触发 429。

对未知模型不猜阈值，这一点与 Codex 一致。但 `builtin:minimax` 的 base URL（`api.minimaxi.com`）和模型 ID 都由 Loom 自己的目录固定（`loom_model_bridge.py:28-40`）。这里不是根据任意模型名称推断，而是 Loom 应当负责声明的内置元数据。

**[实测] 前缀缓存基本无法命中。**

相关探针为 `probe_prefix_stability.py` 和 `probe_request_layout.py`。

- 以有计划的请求为例，历史记录前面有 6 条系统消息：主提示词、`loom_runtime_state`、语言、计划和两条贴纸消息。项目说明、执行提示和浏览器契约存在时，也会插到这里。
- 第 2 条 `loom_runtime_state` 包含 `model_step` 和 `step_id`（`context_state.py:121-126`），每一步都会变化。
- `loom_task_plan` 包含 `execution_since_plan_update`，每次工具结果后都会变化。
- 因此，相邻两步只共享主系统提示词这一条前缀，约 8K 字符。后面的 15 万 token 历史每一步都要重新 prefill。
- 从 1.0.27 起，跨回合时连第 0 条消息也会变化（§5）。
- Loom 没有读取 provider 返回的缓存命中 token（`openai_runtime.py` 的 `_usage_from` 只读取 prompt 和 completion token），所以现在无法回答实际缓存命中率。MiniMax 是否支持前缀缓存，需要实测确认。

**[源码] Codex 的做法。** Codex 第一次注入完整上下文；之后只把变化作为新的历史条目追加：“Full initial context resets the baseline; later turns persist only its changes”（`codex-rs/core/src/session/mod.rs:4625-4700`）。它还会为每个会话设置 `prompt_cache_key`（`codex-rs/core/src/client.rs:575`）。因此，Codex 的历史只追加，请求前缀保持稳定。

**[实测] 外部状态在回合之间丢失。**

- turn 1 因 429 结束后，两个浏览器 session 被关闭。
- turn 2 中，模型只拿到 `KeyError: browser session not found`，于是推断“A 后端在我做 isolated 压力期间也 idle 超时/被清理”。
- 实际上，isolated session 在 06:58:57 仍成功执行过操作，16 秒后随 `turn_failed` 被关闭。

**[源码] 压缩摘要也会延续错误结论。** 10-05 01:55 的检查点摘要把“event #13 isTrusted=false”写成 A-10 和 A-16 失败的“关键证据”。这正是 10-04 文档中已经确认的误读：页面旧事件被当成了新动作的结果。`964ae111` 已把摘要拆成“工具事实、助手解释、用户更正”，但之后还没有新的压缩样本，暂时无法验证效果。

### 2.5 浏览器 session 失效

**[源码]** `BrowserRuntime._record` 在任何回合终止事件中都会调用 `close_owner`，包括完成、失败、取消、中断和超限（`browser_runtime.py:965-977`）。也就是说，浏览器的生命周期被绑定到回合生命周期。因此：

- 模型提前发出一条纯文字进度后，回合完成，session 被销毁。例如 10-05 02:26。
- provider 返回 429 或流式超时后，回合失败，session 被销毁。例如 10-05 06:59。
- 用户只能在提示里专门写：“完成前不要发最终答复、主动结束回合或关闭 session”。

对照：

- **Loom exec 后台进程：** 只会在权限变更、Host 恢复、子代理关闭或运行时关闭时终止，不会在回合结束时清理。同一个系统里，两类长生命周期资源使用了两套规则。
- **[源码] Codex 后台终端：** 只有在用户显式调用 `clean_background_terminals`，或者会话关闭时才会终止。`interrupt` 只中断当前任务（`codex-rs/core/src/session/handlers.rs:57-63, 288-310`）。

`15ee10e3` 增加了墓碑和恢复说明，让模型知道 session 为什么不可用。这个方向是正确的，但它没有改变“回合一失败就丢浏览器”的策略，而且实现中有回归（§5）。

### 2.6 测试结论缺乏证据

**[源码]** `update_plan` 对 `completed` 只检查 `evidence` 是否为非空字符串（`task_plan.py:55`）。它不会检查这段证据是否引用了真实的工具调用或文件。`outcome` 在 `15ee10e3` 中加入，但仍是可选字段。

**[实测] 闲置测试。** 在 `13d05daf` turn 1 中，用户要求：“分别等待 60 秒、180 秒…记录真实等待时长，不把两次等待混为一次”。实际过程如下：

- 60 秒等待正常完成。
- 180 秒的 `Start-Sleep` 在 120 秒时被 exec 默认超时终止，随后模型又补了一次 120 秒。
- 两次 `browser_state` 之间实际空闲约 315 秒（06:42:17 至 06:47:32）。

但记录出现了矛盾：

- 模型写的是：“已等待 60s + 120s = 总计 180 秒闲置”。
- 计划证据写的是：“240s 闲置仍存活”。

这三个数字互相矛盾，而且合并了用户明确要求分开的两次等待。运行时手里有每次调用的精确时间，但没有任何机制把结论和这些事实对应起来。

**[实测] 中断阶段被标成完成。** turn 2 把“isolated 完成 36 动作；is37 KeyError 中断”标为 `completed`，并错误地把 session 消失归因于 idle 超时（见 §2.4）。

**[实测] 判定散落在聊天文字中。** turn 1 的 147 条旁白中，有 60 条写了“通过”，33 条带有“✓”。逐步验收结果散落在聊天文字里，而不是结构化记录中。

### 2.7 流式输出超时

**[实测] 生产日志特征一致。** 从 09-30 到 10-05，所有停滞都表现为：先流式输出 7–15 秒，然后连续 60.0 秒没有内容（`elapsed=68–76s, last_output_gap=60.0s`）。

**[实测] 成功的大写入有明显上限。**

- 两个会话中，所有成功回复的模型耗时最长为 72.0 秒。
- 参数长度在 1 万到 2 万字符之间的 `write_workspace_text` 调用，耗时全部落在 31–72 秒。
- 没有任何成功回复超过约 72 秒。

这与以下解释一致：推理过程会先流式输出，而工具参数要在 60 秒停滞阈值内一次性到达，回复才可能成功。

**[推断，证据较强]** MiniMax 的 OpenAI 兼容接口可能没有增量下发 `tool_calls.arguments`，而是在生成结束后一次性下发。由于该账号当前配额已经耗尽，本次没有发送真实请求。需要先加入分块计时记录，再用真实请求确认（P0-2）。

**[实测] 探针结果。** `probe_stream_and_quota.py` 把 stall 缩放到 1 秒：

- 同样大小的工具调用，如果参数在 1.5 秒后一次性到达，3 次都会触发 `stream_stall_timeout`，回合失败。
- 如果参数分片到达，即使总时长 3 秒，但单次间隔只有 0.15 秒，就会成功。

因此，成败取决于 provider 的分块粒度，而不是连接是否仍然存活。

**[源码] 规则来源。**

- `ModelExecutor(timeout=150, stall_timeout=60, max_duration=900)`（`model_execution.py:50-55`）。
- 只有文字、推理和工具片段会刷新进度（`openai_streaming.py:133`）；空 chunk 不会。
- `cd297fbd`（09-30）对停滞的处理是“同一请求再试 2 次”。如果停滞是确定性的，重试只会把等待时间和 token 消耗放大 3 倍；每次请求输入约 175K token。

**[源码] Codex 对照。**

- 流空闲超时默认 300 秒，并可以按 provider 配置（`codex-rs/model-provider-info/src/lib.rs:63`）。
- 任意 SSE 事件都算活动（`codex-rs/codex-api/src/sse/responses.rs:540`）。
- 流重连默认最多 5 次。
- 用量上限错误直接返回，不重试（`codex-rs/core/src/session/turn.rs:1705`）。

**[实测] 429 配额被当成瞬时限流。**

- `_RETRYABLE_STATUS_CODES` 包含 429（`openai_runtime.py:30`）。
- “Token Plan 用量上限 (2056)”因此会被当成可重试错误。
- 探针中，一次配额错误会产生 9 次 HTTP 调用：`_create` 3 次 × 回合重试 3 次。
- 这些重试不会出现在事件记录中；只有 `ModelRequestTimeout` 会记录 `MODEL_RESPONSE_REJECTED`。

## 3. 结构性问题：为什么修改会层层叠加

### 3.1 用继承链承载功能开关

生产 `AgentRuntime` 实际是 `BalancedStickerStreamingAgentRuntime`，MRO 共 22 层：Sticker → Streaming → CodeMode → Skill → ToolSearch → ConfiguredMCP → BrowserBackendRegistry → SingleLoopComputer → ComputerUse → MCP → BrowserRuntime(v1) → BrowserRuntime → WebSearch → 4 层 Memory → MultiAgent → Context → Sandbox → Durable → AgentRuntime。

184 个方法中有 33 个被多层覆盖：

- **`_prepare_model_request`：7 层。** 每层都向请求追加内容，例如贴纸、浏览器契约与观察、资源恢复说明、工具计划和上下文预算。模型最终看到的内容，只有在运行时打印后才能确认；消息顺序依赖“插在第一个非 system 消息前”这类约定。
- **`_record`：5 层，并带副作用。** 它会关闭浏览器、改写模型文本和历史，并维护流上下文。也就是说，“记录一条事件”会改变外部状态。
- **其他高频覆盖：** `recover_interrupted` 有 8 层，`__init__` 有 20 层，`close` 有 11 层。

新功能只能继续增加子类，并覆盖同一个方法。这就是修改不断层层叠加的直接机制。

### 3.2 导入期补丁导致生产与测试不一致

`app/__init__.py` 安装了 19 个 `sys.meta_path` 补丁器；`import_patch_chain.py` 专门用来处理“两个补丁争夺同一模块”的情况。App Server 在构造 service 时，还会对 `type(self.runtime)` 打补丁（`live_steering_contract.py:487`，`live_steering_interrupt_contract.py:211`）。

**[实测]** 直接调用补丁函数后，对比结果如下：

| 方法 | 直接构造 `AgentRuntime` 的测试中 | App Server 中 |
| --- | --- | --- |
| `_model_system_prompt` | `runtime.py` | live_steering_contract 闭包，会追加转向说明 |
| `_consume_steering` | `runtime.py` | live_steering_contract 闭包 |
| `steer` | `runtime.py` | live_steering_interrupt_contract 闭包，内部又包裹前一层闭包 |
| `_before_turn_completed` | 不存在 | live_steering_interrupt_contract 闭包 |
| `resume_steered_turn` | 不存在 | 两层闭包 |
| `_track_goal_usage` | `durable_runtime.py` | 闭包 |

`TurnRunner` 通过 `getattr(rt, "_before_turn_completed", None)` 兼容这两种情况。因此，对这 6 个方法来说，阅读 `runtime.py` 看到的代码和直接测试 `AgentRuntime(...)` 验证的代码，都不是生产实际运行的代码。

### 3.3 同一事实有多个版本

- `runtime_authored: False` 与贴纸改写后的实际文本不一致。
- 计划中的 `evidence` 是自由文本，可能与工具结果不一致（§2.6）。
- `docs/turn-completion.md` 仍写着“The canonical TurnRunner runs a private, read-only Stop hook…”，与 `turn-completion-lifecycle.md` 中“可选、默认关闭”的说明相反。
- `turn_stop.py`（382 行，一周内 +422/-40）和 `turn_continuation.py` 在生产中处于关闭状态，但仍保留在 `TurnRunner` 主路径的分支里。

### 3.4 每次请求重复解析完整事件日志

请求准备路径中有多处调用 `store.events(session_id)`，例如 `runtime.py:513`、`browser_runtime_v1.py:492`、`context_budget.py:189`。每次调用都会在文件锁下读取并解析整个 `events.jsonl`；`2d6e4fca` 的日志为 6.9 MB。

目前请求准备耗时中位数为 180–315 ms，最高 4.3 s。这还不是主要延迟来源，但会随会话增长线性变慢。

### 3.5 发布没有被 CI 拦住，当前测试仍有失败

- 我抽查了 `dd86ccd5`（10-04 晚）到 `15ee10e3` 之间的 13 次 main CI，全部失败。其中 Host 1.0.22–1.0.27 的源提交（`a4ff5eef`、`394af171`、`d1891e46`、`4541ff9a`、`d57e156b`、`15ee10e3`）CI 都失败了，但 “Loom Host Runtime Release” 仍照常成功并完成发布。
- 10-03 至 10-05 共发布了 21 个 Host 版本。部分运行使用了中间版本，因此事后需要单独确认每次失败对应的具体版本。
- 当前有 2 个失败测试：
  - `test_terminal_response_recovery::test_turn_recovers_an_unclosed_decision_block_before_commit`：至少从 `a4ff5eef` 起失败，这个提交删除了代码块和括号截断推断。去掉“代码块未闭合即判定截断”这类基于文字形状的推断是对的；但 `loom-decision` 是 Loom 自己的结构化协议，UI 需要解析其中的 JSON。对它做结构校验，与工具参数校验性质相同，并不是猜测普通文字。需要明确选择：恢复对 decision 块的结构校验，或者删除测试并接受未闭合的 JSON 被渲染成坏卡片。
  - `test_header_composer_contract::test_header_status_is_visible_and_state_driven`：`3b8c8a00` 删除标题状态点后，测试没有同步更新。

## 4. 已证实有效的修改

| 提交 | 修改 | 证据 |
| --- | --- | --- |
| `394af171` | 工具不可用或参数错误时，返回带 call_id 的工具观察，不再消耗模型重试次数 | 修复前，`2d6e4fca` 有 16 次 `invalid_tool_arguments` 被驳回；其中一个回合累计 6 次，最终导致回合失败。1.0.23 下，`13d05daf` 的 4 次参数错误都变成 `tool_failed(not_executed)`，回合继续执行 |
| `cb239cc8`、`8b00b21b` | 收尾审查改为可选，去掉 3 次驳回上限 | 1.0.18 之后，没有再出现 `turn stop assessment failed`，也没有再出现因审查上下文超限导致的 `limit_reached` |
| `d1891e46` | 删除每 8 次工具调用自动插入的旁白，并删除基于文字形状的截断推断 | 代码已删除；副作用见 §3.5 |
| revision / `stale_observation` | 浏览器动作使用过期索引时不执行，并返回最新观察 | `13d05daf` 的 50 次压力动作中，有 1 次被拒绝且如实记录，没有误操作 |
| `15ee10e3` 墓碑与 `browser_session_unavailable` | 告诉模型 session 不可用的原因 | 方向正确，但还没有真实运行验证；存在回归（§5） |

## 5. 需要立即修复的回归（1.0.27 已在本机激活）

当前实现会产生两个问题：

- `BrowserRuntime._record` 会给每个终止事件写入 `browser_resources: {state: released, resume_requires_new_session: true}`，不管这个会话是否打开过浏览器。
- 只要上一个回合的终止事件包含这个字段，`browser_runtime_v1._prepare_model_request` 就会把 `loom_resource_resume` 放在请求最前面，位于主系统提示词之前。之后每个回合的每一次请求都会重复这样做。

探针 `probe_history_injection.py` 证实：在一个从未使用浏览器的三回合会话中，第 2、3 回合的每次请求都以“Recorded prior-turn resource lifecycle… resume_requires_new_session: true”开头。

影响：

- 无关任务中会出现误导性的浏览器说明；
- 主系统提示词不再位于首位；
- 每个回合的内容都不同，因为其中包含上一回合的 id 和时间，导致跨回合前缀缓存全部失效。

建议修复：

1. 只有当 `close_owner` 实际关闭至少 1 个 session 时，才写入资源释放记录，并带上数量和 browser_id。
2. 只在新回合的第一次请求中注入一次。
3. 把这条信息放在主系统提示词之后，或者作为一次性条目追加到历史中，而不是常驻在请求前缀。

## 6. 与 Codex 对照：哪些有依据，哪些没有

以下内容有公开源码依据（`openai/codex@a7660cd1`）：

- **回合继续条件：** 有工具调用、`end_turn=false` 或有待处理输入时继续；否则结束，并运行可配置的 Stop hook（`session/turn.rs:566, 653-700`）。Loom 当前在这一点上与 Codex 一致。
- **流式与错误处理：** 流空闲超时默认 300 秒，并可按 provider 配置；任意 SSE 事件都算活动；流重连默认 5 次；用量上限错误不重试（`model-provider-info/src/lib.rs:63-65`，`codex-api/src/sse/responses.rs:540`，`session/turn.rs:1705`）。
- **上下文：** 首次注入完整上下文，之后只追加变化；按会话设置 `prompt_cache_key`（`session/mod.rs:4625-4700`，`client.rs:575`）。
- **后台资源：** 后台终端跨回合保留，只在显式清理或会话关闭时终止（`session/handlers.rs:57-63, 288-310`）。
- **进度表达：** GPT-5.1 提示词要求进度更新为 1–2 句，并且“no more than 8-10 words”；同时要求“Persist until the task is fully handled end-to-end within the current turn”（`gpt_5_1_prompt.md:30-46, 186-192`）。
- **工具执行时机：** 流式过程中，工具项一到达就开始执行（`OutputItemDone` → in-flight futures，`session/turn.rs:2688`）。Loom 要等整条回复完成后才执行工具。

以下内容没有依据，不应写成结论：

- Codex 桌面版的进度 UI 和私有服务端行为；
- “Codex 模型不会提前结束”：这是关于模型训练的推断，公开运行时中没有对应的防护代码；
- MiniMax 的流式行为：这是根据 Loom 日志得出的推断，与 Codex 无关。

## 7. 改进方向（按优先级）

### P0：改动小，直接针对已观测到的失败

1. **修复 1.0.27 回归**，修法见 §5。
2. **重做流式停滞判定。**
   - 把连接存活检测和内容停滞检测分开。任何字节或 chunk，包括空 delta，都应算作连接活动，并由传输层判断。
   - 内容停滞阈值按 provider 配置，默认值对齐 Codex 的 300 秒。
   - 如果 provider 会一次性下发工具参数，阈值至少要能覆盖最大的一次写入。
   - 如果 provider 在生成期间完全不发送字节，还需要同步调整 httpx 读超时；当前由 `--timeout` 控制，默认 120 秒。否则，即使放宽停滞阈值，也会被传输层超时截断。
   - 如果仍然发生停滞，重试时必须改变条件，例如明确提示“上次在生成大段工具参数时超时，请分块写入”；否则最多只重试一次。
   - 在 `MODEL_RESPONSE` 和 `MODEL_RESPONSE_REJECTED` 中记录：首块时间、最大块间隔、首个工具片段时间、工具参数片段数、raw chunk 数和 finish_reason。缺少这些字段，正是这个根因持续一周仍未被定位的原因。完成记录后，用一次约 20 KB 的写入任务，在真实 MiniMax 上确认分块行为。
   - UI 显示“模型仍在生成，已有 N 秒没有输出”，避免界面长时间静止。
3. **区分截断与格式错误。** 解析工具参数前先检查 finish_reason。如果是 `length`，返回 `incomplete_finish:length`，并告诉模型“工具调用被输出上限截断，请拆分”，而不是说“格式错误”。
4. **在主提示词中说明回合协议。** 只需增加一句：“不带工具调用的回复会结束本回合。只要还有工作，就在同一条回复里发出下一个工具调用；不要以‘接下来我会…’结尾。”这是协议说明，不是关键词判断。
5. **配额耗尽时不要重试。** 识别 429 中的配额错误，例如 MiniMax 2056、usage limit 或 quota，并标记为不可重试。所有传输重试都应写入事件记录。

### P1：解决已证实的设计冲突

6. **把浏览器生命周期改为会话级租约。**
   - 回合结束时，只释放对用户真实浏览器标签的独占控制。
   - Loom 自己启动的隔离浏览器保留到空闲 TTL（例如 15–30 分钟）、权限变更、会话归档或 Host 关闭。
   - 下一回合可以继续使用同一个 browser_id，这与 exec 后台进程和 Codex 后台终端的语义一致。
   - 如果坚持使用回合级生命周期，至少不能因为 provider 失败就释放 session。
7. **为内置模型声明上下文窗口。** Loom 自有的模型目录（例如 `builtin:minimax`）应提供窗口大小和建议的压缩阈值。另设一个以成本为优先的“工作预算”，例如 100–128K 就压缩，并与硬窗口分开。
8. **稳定请求前缀。**
   - 把每一步都会变化的字段移出历史前面的固定区域，包括 `model_step`、`step_id`、`execution_since_plan_update` 和贴纸场景。
   - 这些字段可以放在历史之后，或者参考 Codex，只在变化时作为历史条目追加。
   - 记录 `cached_tokens`，并用实际缓存命中率验收。
9. **把贴纸移出执行链路。**
   - 带工具的请求不注入贴纸协议。贴纸位置由展示层根据最终答复自行决定；现有 fallback anchor 逻辑已经具备这项能力。
   - 贴纸只作为展示层的旁路数据，不得改写 `session.messages`。
   - `runtime_authored` 必须如实记录。
10. **检查证据引用是否真实存在。**
    - `evidence` 必须引用本会话中存在的 call_id 或工作区文件。运行时负责校验引用存在，以及对应调用确实执行过。
    - 测试类步骤标为 `completed` 时，必须填写 `outcome`。
    - 提供 `record_check(case, expected, observed, verdict, evidence_call_ids)`，写入结构化台账，最终报告由台账生成。
    - 这里只检查引用是否完整，不做语义判定。

### P2：收敛架构，避免下一轮继续叠层

11. **使用统一的请求组装器。** 由 `ContextComposer` 按“固定前缀 → 历史 → 易变尾部”的显式顺序组装请求。各功能以 section provider 的形式注册，替代 7 层 `_prepare_model_request` 覆盖。每次请求都记录 section 清单和大小。
12. **增加明确的回合生命周期钩子。** 把浏览器释放、贴纸收尾和流上下文清理从 `_record` 中移出，放到 `on_turn_end(outcome)`。这些处理应按顺序执行，并且可以单独测试。
13. **去掉运行时类的导入期补丁。** 将 live_steering、continuity、compaction 等补丁合并回运行时类本身。增加一个测试，断言生产运行时类上没有任何方法来自补丁模块；运行时测试也应使用与 App Server 相同的构造路径。
14. **清理休眠代码和过期文档。** 收尾审查要么移出主路径，作为独立扩展，要么删除；同时修正 `docs/turn-completion.md`。
15. **增加发布门禁。** Host 发布必须依赖 CI 通过，并在事件中记录 Host 版本，方便事后定位问题。
16. **缓存事件投影。** 请求准备阶段按回合缓存事件投影，避免每一步多次完整解析 `events.jsonl`。

## 8. 复验方法

修复前后都使用同一个浏览器验收任务、同一后端和同一模型配置，并记录以下指标：

- 终止原因分布：provider_end_turn、legacy_finish、stall、quota、truncation；
- 每次请求的最大块间隔、首个工具片段时间和停滞次数；
- 每步输入 token、缓存命中 token，以及单回合总输入 token；
- 带旁白步骤比例、旁白中位长度和贴纸数；
- 计划更新次数、`outcome` 覆盖率，以及证据引用能被解析的比例；
- 回合边界前后，浏览器 session 是否仍然存活。

单元测试只能证明状态机不会丢失状态；上述指标才能说明问题是否真正解决。每项修复都应先写一个当前会失败的测试，再修改代码。本次的探针可以直接改写成回归测试。

## 9. 修复执行记录与处理状态

### 阶段 0–1（2026-10-05）

代码提交：`7e3f4810`。本阶段只完成基线和止血，**阶段 2–8 未完成**。
Host 发布必须先通过同一源提交的完整 pytest。之后按用户确认的发布调整
`db65c714`，源码推送只运行 CI，Host stable 改为从 main 手动发布。
CI、发布状态及版本可分别查询 GitHub Actions 和 `host-runtime/stable.json` 的
`version` / `sourceSha`；不能把源代码提交当成已安装的 Host。

复核与基线：

- 五个本地探针已运行，确认资源提示、历史贴纸改写、易变前缀、截断分类与额度重试的问题。
- 分片探针返回 `completed`，但 `report_written=False`。其调用使用 `content` 字段，
  工具实际要求 `text`；因此这个探针只能证明流式采样能够完成，不能证明写入成功。
  阶段 2 正式测试必须同时断言工具回执和文件内容。原审计脚本和用户日志未修改。
- MiniMax 一次性下发参数仍是推断；未发送真实模型请求。
- 本地基线：2110 通过、4 失败、6 跳过。除审计的两项失败外，最近 UI 提交还使
  样式加载顺序和可选桌面文案的旧契约失败。最新 CI `37334911133` 也确认这四项。

修改与测试：

- `test_resource_resume_contract.py` 使用生产导出的 `AgentRuntime`，覆盖没有浏览器的
  三回合和真实释放后的三回合。释放事件包含准确数量和 ID；提示只在下一回合第一次
  请求尾部出现。旧 Host 无 ID 的错误元数据不再触发提示。
- 保留 `test_failed_turn_releases_resources_with_typed_reason`；阶段 1 暂未改变回合
  结束释放浏览器的策略，正式会话租约迁移仍在阶段 5。
- `test_agent_request_layout.py` 打印完整请求并锁定当前生产请求的顺序、权威状态和
  关联工具回执。它不是 App Server 补丁一致性测试；那部分仍在阶段 3。
- `test_terminal_response_recovery.py` 通过现有部分输出合并恢复路径修复未闭合卡片。
  只校验显式 `loom-decision` 协议，不猜任务是否完成，不拒绝普通 Markdown。
- `tests/fixtures/decision_protocol.json` 的 13 个案例同时驱动后端验证和实际前端解析器。
  前端不再给缺失 ID 编造默认值；超过六个选项不再被静默截掉。
- `test_host_release_gate.py` 确认发布 job 的 `needs: test`，并确认该 job 与 CI test
  的步骤一致，checkout 没有切换到其他 SHA。路径过滤、手动触发和 stable 推进保持。

旧测试的有意变更（未删除或放宽验收）：

| 测试 | 旧断言 | 依据与新断言 |
| --- | --- | --- |
| header status | 必须有标题状态点 | `3b8c8a00` 已有意删除它；明确断言它不存在，状态 chip 的 tone 与 label 仍由 state 驱动 |
| composer refinement order | refinement 必须是最后一张样式表 | `870e93c2` 新增最后加载的 workspace geometry；锁定最后两个 import 和 geometry 规则，保留所有基础样式先加载的断言 |
| optional desktop copy | 必须包含 `Desktop is optional` | `23f97180` 调整文案；精确断言两种语言都说明 Local Host included、Desktop optional |
| browser-free provider failure | 失败必须带浏览器释放字段 | 原断言要求虚构释放；改为明确无释放字段，保留状态码、不可重试和持久历史断言 |
| signed-out model access | 登录检查必须是单行 `if` | `aa51b0b3` 改成 braced guard 并添加 model readiness；执行实际 `runRpcCall` 函数，断言未登录时不初始化、不调用，已登录时 auth → readiness → dispatch 顺序正确 |

验证证据：

- 修复前：新增资源 / 门禁及原有契约集 5 失败、13 通过；扩展决策集 9 失败、13 通过；
  前端共享协议案例 4 失败、9 通过。
- 修复后：针对性 Python 68 通过；本地全量 **2132 通过、0 失败、6 跳过**；
  前端类型检查和构建通过，流式 / 决策测试 **20 通过**。
- 临时撤去本阶段实现（保持最终测试），Python **12 失败、14 通过**，前端 **4 失败、
  9 通过**。随后逐字节恢复实现并运行上述全量验证。
- 本地全量仍有 10 条既有警告，主要是 Windows 子进程 UTF-8 解码与异步 transport
  清理；未据此宣称链路完全干净。跳过项和跨平台检查由 CI 进一步验证。
- 日志留在忽略目录 `scratch/agent-chain-*.log` 和对应 junit XML；未纳入版本库。
- 首次远端 CI 的 Python 与 Windows / browser / MCP 检查通过，React 在上述旧单行
  登录契约失败。随后更新该契约，完整 desktop scripts **61 通过**、Electron **59 通过**、
  portal release **3 通过**，已验证构建产物。该改动不修改任何鉴权实现。

### §5 / §7 状态表

| 条目 | 状态 / 提交 | 验证方式 | 真实运行复验 |
| --- | --- | --- | --- |
| §5、P0-1 资源恢复回归 | 阶段 1 完成，`7e3f4810` | `test_resource_resume_contract.py`；typed failure 正向测试 | 更新 Host 后确认普通会话无误提示 |
| P0-2 流式活性、配置、计时 | 阶段 2 完成，`10f97b7b` | `test_stream_activity_contract.py`；真实工具写入断言、停滞恢复与既有 deadline 测试 | 额度恢复后 20KB 写入，采集块 / 内容间隔 |
| P0-3 截断分类 | 阶段 2 完成，`10f97b7b` | `test_truncated_tool_adapter.py`：流式 / 非流式、格式错误与生产恢复路径 | 复验恢复路径，不执行半截调用 |
| P0-4 v10 回合协议 | 阶段 2 完成，`10f97b7b` | 精确默认迁移、自定义保留、App Server 构造后的生产请求 | 统计未完成计划下 legacy_finish 与旁白长度 |
| P0-5 额度分类 / 重试归属 | 阶段 2 完成，`10f97b7b` | `test_provider_quota_contract.py`：单次 HTTP、限流回执、Retry-After | 对照结构化错误与事件，不额外消耗额度 |
| P1-6 浏览器会话租约 | 阶段 5 待做 | 阶段 1 保留原容量 / owner cleanup 行为 | 回合失败与恢复、TTL、容量淘汰 |
| P1-7 模型窗口 / 工作预算 | 阶段 4 待做 | profile / compaction 契约与官方元数据复核 | 每步输入量与压缩后更正保留 |
| P1-8 稳定请求前缀 / 缓存计量 | 阶段 4 待做 | 固定当前布局；前缀探针保留 | 读取实际 cached token，不能由布局推算命中率 |
| P1-9 展示贴纸与规范历史分离 | 阶段 6 待做 | 历史改写探针保留 | 旁白比例与长度、最终展示偏好 |
| P1-10 证据引用 / record_check | 阶段 7 待做 | call_id / 文件引用和时间差校验 | 同一验收任务报告对照调用事实 |
| P2-11 唯一请求组装器 | 阶段 4 待做 | 比较阶段 0 布局，记录有意差异 | 模型切换与长任务复验 |
| P2-12 显式生命周期钩子 | 阶段 5 待做 | side effect 顺序与隔离测试 | 取消、失败、跨回合资源保留 |
| P2-13 移除四个运行时补丁 | 阶段 3 完成，见下节验证 | App Server / direct 行为与方法来源守护 | 转向、压缩与恢复链路 |
| P2-14 休眠 Stop 路径 / 文档 | 阶段 8 待做 | 构造路径审查后删除或隔离 | 无额外收尾审查 |
| P2-15 Host 发布门禁 | 阶段 1 完成，`7e3f4810` | same-SHA test 依赖；GitHub CI / Release 实际运行 | 核对安装版本与 sourceSha |
| P2-16 事件投影缓存 | 阶段 8 待做 | 增量投影及 preparation_ms 对比 | 长会话请求准备时延 |

### 阶段 2（2026-10-05）

源码提交：`10f97b7b`。本节完成采样可靠性；阶段 3–8 的结构性工作仍未完成。
本阶段没有添加完成关键词分类、工具次数终止或隐藏审核，也没有发送真实模型请求。

主要路径：

- `execution_control.py` 分别记录任意原始 chunk 与实质内容进展。OpenAI、
  OpenCode Go Responses / Messages 的原始读循环都报告活性；规范流层为不报告
  原始 chunk 的后端提供一次计量，不重复统计已有原始计量。
- `profiles.py` 提供单一 300 秒默认值，通过 ModelBinding → provider catalog →
  ModelProfile 传递可配置空闲阈值；HTTP 读超时不短于它。显式 executor 参数仍可
  用于缩时测试。首输出、内容停滞、最大时长保留不同的错误原因。
- `model_execution.py` 将首块、首内容、首工具参数、最大间隔和片段数附到成功响应
  及失败异常；TurnRunner 将它们写入响应 / 驳回事件。临时活动通知每两秒更新 UI
  的无输出秒数，不写入持久聊天历史或每秒追加事件。
- OpenAI 流式与非流式适配器在解析 JSON 前识别显式输出上限；半截调用不会执行，
  驳回包含 finish_reason、工具名和收到的参数字符数，恢复要求分次写入。
- 删除后端的三次隐式重试。TurnRunner 统一负责语义重试、退避和 Retry-After。
  额度耗尽不可重试；MiniMax 业务码识别在代码中注明实测样本与日期。
  仅保留明确不支持 stream_options / include_usage 时的一次能力协商，并通过
  `MODEL_TRANSPORT_RETRY` 记录；普通 502 / 503 不再触发这条隐式协商路径。
  停滞最多原样重放一次，再次请求附明确的小块写入说明。
- `system_prompts.py` 保留精确 v9 快照，迁移默认值到 v10，说明无工具回复结束回合、
  工具旁进度最多一句。生产 App Server 构造路径已验证，用户自定义提示不迁移。

Codex 依据：固定提交 `a7660cd15490875b8c22f66e577da115ed927fe3` 的
[model-provider-info/src/lib.rs:63](https://github.com/openai/codex/blob/a7660cd15490875b8c22f66e577da115ed927fe3/codex-rs/model-provider-info/src/lib.rs#L63)
声明 300 秒默认值；
[codex-api/src/sse/responses.rs:540](https://github.com/openai/codex/blob/a7660cd15490875b8c22f66e577da115ed927fe3/codex-rs/codex-api/src/sse/responses.rs#L540)
按下一 SSE 事件等待执行空闲超时。本阶段只依据这两处源码，不推测私有桌面实现。

验证：

- 修复前：流式 / 非流式截断新增测试 2 失败；两种额度样本各重放三次，2 失败；
  限流单层归属、独立计时、UI 通知和 profile 配置测试分别失败；v10 版本断言失败。
- 修复后：全量 **2155 通过、0 失败、6 跳过**，9 条既有环境警告；
  前端类型检查通过，流式 / 决策共享测试 **20 通过**。
  20KB 整块和分片场景均驱动实际 `write_workspace_text`，验证文件全文一致、
  工具只执行一次、计时存在；临时 activity 到达订阅者且不进入持久事件。
- 撤去实现但保留测试：**15 失败、10 通过**，随后逐字节恢复。
  日志位于忽略目录 `scratch/agent-chain-stage2-*.log` / XML。
- 首轮全量暴露旧 profile 替身缺少可选元数据，已通过默认值兼容且未修改原断言。
  后续全量暴露竞态测试 executor 替身的旧接口，只增加两个可选观察参数，保留所有
  转向、用量与持久响应断言。另两处旧测试变化是版本 9 → 10，以及进度调用增加
  tool_fragment 参数后的源码断言；提交信息逐条说明了原因。

发布：按新流程只提交 / 运行 CI，不触发 Host 构建；远端 stable 仍为 **1.0.28**，
来源 `10134b5e`。阶段 2 源码尚未进入运行中的 runtime，不能据此评估模型效果。

真实复验（有额度后执行，不由本次测试代替）：

1. 在同一浏览器验收任务里要求一次约 20KB 文件写入，核对文件、工具回执与报告。
2. 采集 `stream_timing` 的 first_tool_fragment_ms、tool_argument_fragments、
   max_chunk_gap_ms 和 max_content_gap_ms，判断是否集中下发参数。
3. 统计未完成计划下的 legacy_finish、旁白比例 / 长度、超时和额度失败分布。
4. 核对 Host 版本和 sourceSha；300 秒配置与伪 provider 通过不能证明 MiniMax
   已遵守协议或任务已经完成。MiniMax 集中下发参数仍是待实测的推断。

下一阶段先锁定生产构造行为，再合并四个运行时补丁；资源租约、前缀、贴纸和证据
规则仍按阶段 4–8 推进，不把阶段 2 当成整条链路已经整理完成。

### 阶段 3（2026-10-05）

本阶段合并导入期行为，阶段 4–8 尚未完成。当前实现归属、保留的 15 个启动集成
及后续处理计划见 [当前架构](agent-chain-current-architecture.md)。

- `steer` 的采样失效、审批替换与消费逻辑合并到 Core；Durable 显式负责目标统计、
  收尾前统计与队列排空；service 类直接实现转向 RPC、附件处理与能力声明。
- 自动压缩入口、手动压缩策略、checkpoint 提交和连续性引用分别归入正式模块。
  helper 模块只进行记录与内容转换，不安装 finder 或改写类方法。
- 移除四个安装器及其方法替换实现。旧连续性模块只保留常量兼容导出，避免让既有
  导入方失效；同一协议常量只有一个定义。
- 全部生产 MRO 的方法来源复查又发现 MCP 层的 `recover_turn_if_idle` 覆盖了
  Durable 入口，绕过目标统计。删除重复恢复循环，MCP 只保留授权验证 hook；
  生产类恢复现在使用 Durable 的用量、队列流程。恢复前记录 usage 基线，仅计
  恢复新增 token，不重复累加旧用量。这是本阶段明确的行为修正。
- MCP 对审批、活跃 owner、未闭合 step / 持久 binding 的拒绝保留；错误类型保持，
  部分错误文案随统一入口调整。没有新用户消息、重放已执行工具或重建进程授权。

验证证据：

- 初始特征化集：原有转向与压缩 **25 通过**；新增方法身份守护 **1 失败**，
  明确复现构造 service 前后替换 runtime 方法。
- 基础 service 与 Streaming service 都验证整个生产 MRO 的方法来源、构造前后
  方法身份、提示 hook 和实际请求一致；生产类直接 / service 两条路径均验证
  采样中断、同一回合恢复和输入去重。
- 新增生产恢复用量测试先失败（实际新增 7 token，目标仍为 0）；扩大测试后验证
  原有 5 token 加新增 7 token 正确为 12，并验证未闭合授权不执行。
- 最终相关测试 **39 通过**。撤去本阶段实现并保留最终测试：**3 失败、30 通过**，
  然后逐字节恢复实现。没有删除或放宽任何原有验收断言。
- 首轮全量：2155 通过、1 失败、6 跳过。唯一失败是未改动的浏览器桥接 origin
  拒绝测试发生 Windows `ConnectionAbortedError (10053)`；单独复跑该文件 10 项通过。
  随后一次完整回归 2159 通过、6 跳过。发现 MCP 入口覆盖后又新增修正和验证，
  该次通过不能代替最终源码的全量检查。最终源码全量 **2163 通过、0 失败、6 跳过**，
  10 条既有 Windows 解码 / transport 清理警告未隐藏；最终日志为
  `scratch/agent-chain-stage3-final-source.log` / XML。
- 前端类型检查通过，流式 / 决策共享测试 **20 通过**。没有前端实现改动。
- 日志与 XML 在忽略目录 `scratch/agent-chain-stage3-*`；全部使用伪 provider，
  没有发送真实模型请求，没有修改用户运行数据或配置。

本阶段不改变请求布局。检查发现 Context 层绕过 `_model_system_prompt`，所以
仅证明转向提示 hook 一致不足以证明实际生产请求包含那段契约。阶段 4 必须以
最终 provider 请求验证稳定契约、预算与尾部上下文；不能用 hook 测试替代它。

源码提交与 Host 发布分离。本阶段不自动发布或激活 Host；仍需后续稳定发布与
真实长任务复验。22 层继承结构、请求组装覆盖、贴纸历史改写及资源生命周期的
剩余工作仍按阶段 4–8 推进，不能据此称整条链路已彻底重构。
