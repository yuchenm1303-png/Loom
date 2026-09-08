# Loom 与开源 Codex：代码逻辑及对齐差距审计

审计日期：2026-09-08。此次只新增审计文档，没有修改产品代码。

## 结论与范围

Loom 已是功能较多的 Agent 原型，有真实的工具循环、进程管理、权限决策、持久队列、MCP、子 Agent 和 UI 分层。主要短板是核心行为尚未闭合，模块之间依赖继承和共享状态串接，“已有模块”不能等同于“已对齐 Codex”。建议保留 Python 实现，先建立可以验证的行为基线，再逐步替换内部结构，不需要为对齐而改写成 Rust。

本次检查主链路、模型协议、上下文、审批恢复、持久化、沙箱、工具与技能、App Server 及测试。Browser/Computer/Memory/MCP 检查了接入边界，没有逐条验证外部服务的真实行为。未运行 Codex 的完整 Rust 测试，也未进行同模型任务成功率对照，因此不提供虚假的“对齐百分比”。

- Loom 检查起点：`a8eb47198995719aa4091c96904af12abb53fc24`。审计开始时只有未跟踪的 egg-info；结束时发现 desktop 的 sidebar_motion/theme/thread_presentation/widgets/window 出现其他来源的工作区修改。因此桌面测试结果代表运行当时的工作区，不能严格归属该 commit，也不能视为这些后续修改的验收结果；本次未覆盖或撤销它们。
- Codex：本次从官方仓库下载并固定到 `d6489472f3c15e87d2d7763a5fde033545c530f8`。
- 旧 `docs/codex-alignment.md` 固定的是 `5ecb3afd...`，其中“current head 仅领先一提交”只适用于旧审计时间。
- 官方 App Server 文档：[Codex App Server](https://learn.chatgpt.com/docs/app-server)。协议同样提供 thread/turn/item、流式事件与审批机制；Loom 的相似命名不意味着线协议兼容。

## 当前真实执行结构

默认导出的 `AgentRuntime` 实际是 `StreamingAgentRuntime`。运行时查看 Python MRO，排除 object 后有 16 个类：

```text
Streaming → CodeMode → Skills → ToolSearch → ConfiguredMCP
→ ComputerUse → MCP → Browser(v1) → Browser(base) → WebSearch
→ Memory → MultiAgent → Context → Sandbox → Durable → Core
```

主执行流程是：

```text
UI / CLI → start_turn → 读取 session + 会话锁
→ 构建 StepContext / ToolRouter / transient context
→ 同步 execute_chat（可在内部消费流）
→ 保存 assistant response
→ 顺序处理 pending_tool_calls
→ Orchestrator 校验与权限决策
→ 执行或保存 pending approval
→ 保存工具结果 → 下一次采样，或标记 completed
```

这条链具备正确的基本方向，但中断、失败、配置变化和长上下文时的行为还不能视为成熟。

## 关键发现

### 1. P0：截断响应被误报为任务完成【已复现】

位置：`app/agent_runtime/context_runtime.py:295,330`；基类 `runtime.py` 有对应逻辑。

`finish_reason` 被记入事件，但没有控制终态。模型只要返回非空文本且没有工具调用，就进入 `COMPLETED`。

使用默认完整运行时和假模型返回 `ModelResponse(text='unfinished output', finish_reason='length')`，实际结果为 `completed`。这意味着输出被 token 上限截断，也会向 UI 和目标调度层报告成功。

Codex 对 `response.incomplete` 明确产生错误，并区分 completed 事件：见 [responses.rs](https://github.com/openai/codex/blob/d6489472f3c15e87d2d7763a5fde033545c530f8/codex-rs/codex-api/src/sse/responses.rs#L472)。两者 API 不同，应对齐“截断不得等同成功”的语义。

建议：引入标准化 ModelOutcome（正常结束、截断、拒绝、可重试错误、取消），由统一 Turn 状态机消费；不要依赖 provider 原始字符串直接判断。

验收：length、content filter、断流、空响应、完整响应分别有明确终态；截断不能显示成功或自动完成目标。

### 2. P0：审批恢复沿用 step_id，却重建工具绑定【已复现】

位置：`runtime.py:227`、`storage.py:145`、`tool_search_runtime.py:161`。

审批持久化记录包括 call_id、工具名、参数、effect 和 reason，未包括工具/schema/配置版本或完整解析后的 step 设置。`resume_approval` 使用当前 registry 重建 StepContext。

实验：旧 runtime 对 `audit_tool` 进入 waiting_approval；关闭后用同名但不同 handler/description 的工具启动新 runtime；批准旧 call_id。结果执行的是 **new handler**，没有识别绑定已变化。这证明“同一个 step_id”并不能证明“同一份执行上下文”。实验没有执行真实文件写入。

Codex 的 request snapshot 同时捕获 settings、MCP binding、tool router、环境和 AGENTS.md，见 [step_context.rs](https://github.com/openai/codex/blob/d6489472f3c15e87d2d7763a5fde033545c530f8/codex-rs/core/src/session/step_context.rs) 与 [step_settings.rs](https://github.com/openai/codex/blob/d6489472f3c15e87d2d7763a5fde033545c530f8/codex-rs/core/src/session/step_settings.rs)。不据此声称 Codex 支持任意跨进程审批续接；Loom 既然提供此能力，就需要自行保证绑定不漂移。

建议：持久化可序列化执行描述及版本，包括模型、权限、环境策略、工具来源/schema/MCP 配置指纹；恢复时验证一致性。无法恢复同一语义时使旧审批失效，而非静默替换。

验收：同名工具换 schema、MCP endpoint 变化、权限策略变化、模型配置变化后，旧审批不得无提示执行新的绑定。

### 3. P1：取消未贯穿模型 I/O；缺少运行中 steering【部分已复现】

位置：`runtime.py:293`、`context_runtime.py:266`、`app/ai/openai_streaming.py:49`、`app/app_server.py:1032`。

CancellationToken 在采样前后检查，但没有传入 execute_chat，流读取也没有消费该 token。用阻塞假模型测试：cancel 返回时状态仍为 running，模型工作线程仍存活；释放假模型后才结束。这个实验验证的是控制契约，不是实际 HTTP 取消延迟的基准。

Loom 有未来回合队列，但 App Server 各层 dispatch 没有 `turn/steer`。队列等待下回合和修改当前回合的方向是两种能力。

Codex 采样请求与流等待使用 cancellation token，并有 stream retry 状态，见 [session/turn.rs](https://github.com/openai/codex/blob/d6489472f3c15e87d2d7763a5fde033545c530f8/codex-rs/core/src/session/turn.rs#L1435)；steer 在 [协议定义](https://github.com/openai/codex/blob/d6489472f3c15e87d2d7763a5fde033545c530f8/codex-rs/app-server-protocol/src/protocol/common.rs#L1022) 中明确存在。

建议：ModelTransport、工具执行、等待和退避共享取消机制；区分 cancel_requested 与 cancelled；在安全采样边界消费 steering。不能仅让 UI 按钮立即变灰。

### 4. P1：上下文按消息条数限制，没有自动压缩闭环【源码确认】

位置：`context_runtime.py:142,164,247`。

现有 checkpoint/语义压缩是实质性基础，但活动回合和待审批回合不能调用压缩；drive 超过 max_messages 直接 LIMIT_REACHED。没有根据模型窗口、工具 schema、图片和工具输出预估上下文，也没有在长回合内自动压缩后继续。

因此两条超大消息可能先撞模型窗口；很多短消息则可能不必要地触发本地停止。手工压缩能力不能替代长任务运行时的自动管理。

Codex 的 token 状态与自动压缩进入采样循环，见 [session/turn.rs](https://github.com/openai/codex/blob/d6489472f3c15e87d2d7763a5fde033545c530f8/codex-rs/core/src/session/turn.rs#L481)。

建议：先实现 provider-aware token 估算和输出预留，再在完整 tool group 边界压缩；保留近期用户约束、未完成工作、工具配对、当前配置来源。压缩失败需有明确恢复策略。

### 5. P1：缺少 AGENTS.md 项目指令加载；Skill 只是发现和读取【已复现/源码确认】

位置：`context_runtime.py:209`、`skills_runtime.py:12`。

搜索运行时代码没有发现 AGENTS.md / AGENTS.override.md 自动发现逻辑。临时项目写入唯一 AGENTS.md 标记后，实际首个模型请求未包含标记。模型可能自行读文件，但这不是可靠的项目指令契约。

SkillRuntime 提供 skill_search / skill_load；尚未形成与当前 step 绑定的技能选择、激活及压缩后存续规则。因此不能把“可以读取 SKILL.md”评价为完整的指令生命周期对齐。

Codex 提供项目根到 cwd 的指令发现、override 和字节预算，见 [agents_md.rs](https://github.com/openai/codex/blob/d6489472f3c15e87d2d7763a5fde033545c530f8/codex-rs/core/src/agents_md.rs)。

验收：多层目录、override、无 Git 项目、信任状态、预算限制、恢复与压缩之后的指令来源可解释且一致。

### 6. P1：持久化存在双写裂缝，事件日志没有损坏尾部容错【部分已复现】

位置：`runtime.py:685`、`storage.py:230,256,274`。

每个事件先 append+fsync events.jsonl，再重写整个 session.json。两者不是同一事务。若发生中间崩溃，UI/audit feed 与恢复 snapshot 可能不一致。队列 SQLite 事务与工具历史修复已经存在，但并不能自动解决所有事件/snapshot 双写问题。

向临时事件日志追加半行 JSON 后，`store.events()` 直接抛 JSONDecodeError，不能读取此前有效事件。这是已复现问题；双写崩溃窗口属于源码可见风险，本次没有 kill -9/断电注入。

建议：先明确唯一权威状态，使用事务事件表或带提交序号的 journal+snapshot；容忍末尾未提交记录；不为外部工具副作用承诺无法实现的 exactly-once；标记结果未知并提供对账路径。

每条事件重写全量历史也构成长对话写放大，属于复杂度分析，本次未测实际性能。

### 7. P1：结构依赖深继承，修改生命周期容易发生联动【架构判断】

位置：`agent_runtime/__init__.py:161`、`runtime.py:326`、`context_runtime.py:230`、各扩展 runtime。

16 个类的 MRO 包含 ConfiguredMCP 多继承和两个 BrowserRuntime；不同层覆盖 start_turn、resume_approval、cancel、close、_build_step_context、_request_context_messages。Core/Context 各维护一份 drive 循环。新逻辑可能加到一份而遗漏另一份，也可能依赖不明显的 super 调用顺序。

深继承本身不是错误；问题是核心行为缺少单一归属。建议演进到一个 TurnRunner + 组合服务：ContextManager、ToolExecutor、PermissionResolver、ThreadStore、ModelTransport。Browser/Computer/MCP/Memory 作为服务注册，不通过增加父类改变生命周期。

先抽出并统一两份 drive，在行为测试保护下逐模块迁移；不要一次性整体重写。

### 8. P1/P2：权限模型仍偏粗，沙箱完成度必须按平台与实测分开【源码确认】

位置：`permissions.py:65,109,173`、`sandbox.py:103,192,277`、`process_tools.py:40`。

已有 PermissionSnapshot 是值得保留的基础。但授权主要仍是 READ_ONLY / MUTATING / SENSITIVE 的 effect 分类与四个 preset；缺少细粒度网络目标、额外读写根、命令规则及统一 shell environment policy。读命令也可能因整体归类为 sensitive 而要求批准。

Windows 已有 MXC wxc-exec probe、容器配置和网络隔离声明；“Windows 沙箱没有实现”是旧文档错误。当前策略 AUTO 在后端不可用时允许无 OS 隔离执行，REQUIRED 才拒绝；Linux 分支明确 network_isolated=False。应按产品声明选择默认行为，不能把 workspace 名称当成始终隔离的保证。

本机 MXC 真机测试因未配置 LOOM_WINDOWS_SANDBOX_EXECUTABLE 跳过，故本次不能确认实际隔离效果。已有集成测试文件是进步，但 skip 不能算通过。

Codex 有单独的 [shell environment policy](https://github.com/openai/codex/blob/d6489472f3c15e87d2d7763a5fde033545c530f8/codex-rs/config/src/shell_environment_policy.rs) 和更丰富的 [平台隔离实现说明](https://github.com/openai/codex/blob/d6489472f3c15e87d2d7763a5fde033545c530f8/codex-rs/core/README.md)。不必照搬所有企业选项，但要统一解析权限与实际执行环境。

### 9. P2：工具与模型兼容性还没有明确的对齐层【源码确认】

- 模型后端以 Chat Completions 为主，统一模型结果只有 text/tool_calls/usage/finish_reason 等。SDK 本身可能重试 HTTP 请求，不能说“完全没有重试”；但运行时缺少 Codex 那样的流错误分类、恢复及响应 item 语义。
- apply_patch 是 JSON changes + 精确替换，Codex 是独立 patch 文法和处理器。相同工具名不等于相同格式或模型使用习惯。先决定是否要支持 Codex 格式，再通过 fixture 比较行为，不必为了名称删掉已有原子预检查。
- validate_tool_arguments 是 JSON Schema 子集，未实现 oneOf/anyOf/$ref/minimum 等完整规则。对外部 MCP schema，应该明确兼容范围或使用标准验证库，不能笼统声称完整 schema 校验。
- 普通 pending tool calls 顺序执行；CodeMode 是受限 Python AST 解释器。两者都有用途，但不能直接等同于异步工具调度及完整代码运行环境。
- 子 Agent 有独立会话、图、队列、边界及恢复测试，不应误判为“仅嵌套模型调用”。还应补同模型的父子任务完成/失败/取消/重启组合场景验证。

### 10. P2：App Server 的恢复与背压契约仍薄【源码确认】

位置：`app/app_server.py:1077` 与 streaming/thread-management 扩展。

已经有线程管理、通知、流式工具 identity 和有界队列，是可保留的基础。但队列满时统一丢通知，包括可能关键的终态事件。注释说可由 thread/read 重建，不等于客户端必然知道该重同步。缺少显式 gap/resync 信号的客户端可能停留在旧状态。

建议：关键生命周期事件可靠投递；delta 可以合并；慢客户端收到明确重同步标记；历史读取支持分页/游标，避免每次恢复重放全量。优先做恢复正确性，再追求完整 Codex 协议兼容。

## 本机验证结果与限制

运行了 `python -m pytest -q`：全套运行报告 **10 个失败、4 个跳过**。原配置双重 quiet，没有输出通过总数，本报告不从历史 pytest cache 推算。

- 6 个桌面相关失败：包括 DummyWindow/main_splitter 契约不一致、SidebarMotionController._tab_bar 初始化/事件回调异常、旧标题断言、stream badge 和侧栏行为。部分可能是测试滞后或生命周期问题连锁，不能全部算作独立产品缺陷。
- 3 个 PTY 失败：本机 `winpty.winpty` 缺失，属于安装/环境问题，不能据此判定 ConPTY 实现错误。
- 1 个 Web UI 失败：全套运行时快照只看到 user；单独重跑该用例 **1 passed**。记录为并发/隔离/时序待定位，不宣称已证明稳定缺陷。
- 3 个 MXC 真机测试因后端未配置跳过；另有一项平台相关跳过。
- Python 启动有已有 sphinx .pth 环境警告，未修改全局环境。
- 假模型/临时会话实验复现了截断误报、AGENTS.md 未注入、截断日志读取失败、取消仍等待模型、重启审批执行同名新 handler。测试未调用付费模型接口。
- 临时会话清理还遇到 Windows SQLite 文件占用；这是资源释放待调查信号，不计入上述 pytest 失败，也不作为已定位缺陷。

已有 CI 包含 Linux 主套件、Windows context/PTY/desktop/computer 与 MCP/browser adapter job，不能说项目没有跨平台测试。问题在于还需要更可靠的绿灯基线及跨模块故障用例。

## 对齐实施顺序

| 阶段 | 内容 | 退出标准 |
| --- | --- | --- |
| 0：固定事实 | 固定 upstream SHA；统一文档状态词；修复或明确分类现有失败 | 支持环境有可重复测试结果；实现/测试/未验证分别记录 |
| 1：核心正确性 | 统一 TurnOutcome、结束判定、取消、审批版本绑定、日志尾部容错 | 不误报完成；取消可传达底层；恢复不漂移；有效历史可读 |
| 2：长任务可用性 | AGENTS.md、token 预算、自动压缩、流恢复、steering | 一个跨压缩、插话、工具失败的长回合可以正确继续 |
| 3：收敛结构 | 唯一 TurnRunner；组合服务；统一设置解析和持久化边界 | 删除重复 drive；扩展工具无需增加 runtime 父类 |
| 4：平台与协议 | MXC/PTY 真机验收；细权限；shell policy；通知重同步；MCP/patch 兼容 | 文件/网络隔离实测；断连可恢复；兼容矩阵明确 |
| 5：自行优化 | 工具并发、检索、缓存、Memory、Browser/Computer 效率、UI | 在固定任务集上证明收益且不破坏前述契约 |

阶段 1 的修复尽量小步实施；阶段 2/3 可按局部依赖穿插，不必等到所有功能完成才消除重复循环。

建议将“对齐”定义成以下共同验收场景，而非模块数量：

1. 读仓库规则 → 修改代码 → 测试失败 → 修复 → 给出真实结果。
2. 回合内自动压缩后保留用户约束、有效工具配对和待办。
3. 模型断流不误报成功，重试不盲目重复外部副作用。
4. 工具审批期间重启，并验证工具/权限绑定是否一致。
5. 生成中取消和插话，不继续按旧意图执行后续工具。
6. 子 Agent 失败/取消/恢复能够正确汇总给父任务。
7. 慢客户端或断连重连后，终态和历史可以重建。
8. Windows 中文路径、交互式 PTY、文件与网络隔离在支持环境实测。

比较任务成功率时尽量固定模型、模型参数、仓库初始状态和工具条件；无法使用相同模型时，分别报告 harness 契约结果与端到端表现，避免把模型能力差距误当成 Loom 架构差距。
