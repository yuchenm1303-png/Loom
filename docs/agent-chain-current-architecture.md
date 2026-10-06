# Agent 执行链路当前架构

更新：2026-10-05，阶段 3。阶段 4–8 未完成；本文件记录现状，不是全部重构完成声明。

## 正式实现的归属

| 行为 | 唯一实现位置 | 边界 |
| --- | --- | --- |
| 新指令提交、去重、采样失效、审批替换、消费与恢复 | `agent_runtime/runtime.py` | 活跃路径与终止提交共用锁；运行中的工具完成后再转向 |
| 收尾前目标用量统计、队列排空与安全交接恢复 | `agent_runtime/durable_runtime.py` | 终止事件可见前统计转向续跑的用量；不重放未知结果的工具 |
| 转向内容记录、摘要与回执 | `agent_runtime/steering.py` | 普通函数；不替换任何类方法 |
| RPC 转向、附件与能力声明 | `app_server.py` | 构造 service 不改变 runtime 类型或方法 |
| 压缩提交与人工压缩重试策略 | `agent_runtime/context_runtime.py` | 独立 provider 尝试与确定性回退；检查模型预算后提交 checkpoint |
| 自动压缩及失败回退入口 | `agent_runtime/context_budget.py` | 导入后直接可调用，不依赖导入顺序安装 wrapper |
| 连续性引用与近期工具证据索引 | `agent_runtime/continuity.py` | 只读转换；引用不是用户任务或执行授权 |
| 确定性压缩回退 | `agent_runtime/compaction_fallback.py` | 保留原策略，不新增完成判断或工具执行 |
| 连续性引用的用户消息分类 | `agent_runtime/context_compaction.py` | 显式协议名称排除，不匹配用户正文 |

已删除 `live_steering_contract`、`live_steering_interrupt_contract`、
`compaction_resilience_contract` 的补丁实现，并移除四个补丁器的安装。
`agent_continuity_contract.py` 仅保留常量兼容导出；没有 finder、loader、patch 或安装器。
两个阶段的转向现在在同一个 `steer` 中处理，目标统计归属于 Durable 层。
MCP 层保留 `_validate_idle_turn_recovery` 的授权检查，不再拥有第二套恢复循环。

## 有意差异与尚存问题

- 直接构造 runtime 现在拥有原来仅在 service 构造后才有的完整转向协议。
  service 构造前后方法身份和行为一致；已有生产路径的审批、附件、竞态、队列和用量行为保持。
- 方法来源复查发现 MCP 的恢复覆盖曾绕过 Durable 的目标统计和队列排空。
  此次统一恢复入口后，生产恢复也累计新增用量、处理已经授权的队列；记录恢复前
  usage 基线，避免把此前已计入的 token 再累计一次。MCP 的审批、活跃 owner、
  未闭合 step 与持久 binding 拒绝规则保留；不同层的错误文案统一后仍返回同类拒绝。
- 本阶段没有改变请求布局。检查发现 Context 层的请求组装不调用核心
  `_model_system_prompt`；因此该 hook 中的转向说明不能单凭 hook 测试证明进入完整生产请求。
  阶段 4 的统一组装器必须同时覆盖实际发送的稳定契约与预算计算。
- 生产类的继承链仍然存在，七层请求组装和 `_record` 副作用尚未整理。
  阶段 4 统一请求组装；阶段 5 显式生命周期；阶段 6 分离贴纸展示与历史。
- 确定性回退仍使用原有压缩错误分类；本阶段只移动归属，未修改分类策略。

阶段 4 的缓存验证依据：官方 [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
要求完整渲染前缀匹配，工具定义与相关设置也影响复用。只比较稳定的 system 文本
不足以证明整个前缀可复用，更不能证明实际缓存命中。本阶段未实现缓存计量，
后续需同时验证请求历史、工具 schema 和 provider 返回的 cached tokens。
也已复查固定 Codex 源码
[`session/mod.rs:4635`](https://github.com/openai/codex/blob/a7660cd15490875b8c22f66e577da115ed927fe3/codex-rs/core/src/session/mod.rs#L4635)：
`record_context_updates_and_set_reference_context_item` 在无基线时注入完整上下文，
之后记录上下文变化并更新 reference。阶段 4 需明确 Loom 自己的历史与瞬时上下文
归属，不能把不断变化的 snapshot 插在全部历史前面后仍声称已对齐这种做法。

## 保留的启动期集成

下面 15 个安装入口仍在 `app/__init__.py`。阶段 3 按范围限制未改这些功能，
不能把移除四个执行链路补丁描述为移除所有补丁。

| 保留入口 | 后续处理计划 |
| --- | --- |
| connector_product_config | 连接器配置归属专项：先固定打包环境与授权契约，再显式初始化 |
| model_name_compat | 模型配置专项：将兼容迁移归到配置读取入口 |
| runtime_capability_defaults | 能力配置专项：合入对应后端工厂，保留默认就绪状态契约 |
| codex_mcp_discovery | MCP 专项：合入发现服务，保留用户配置优先级 |
| thread_title_override | 标题专项：统一标题处理入口 |
| thread_title_backfill | 标题专项：统一历史迁移入口 |
| thread_title_rescue | 标题专项：合并失败恢复，避免再次覆盖标题方法 |
| ordinary_conversations | 会话产品专项：直接归入 service 的普通会话入口 |
| project_git_commit | 项目 Git 专项：显式注册项目 RPC |
| project_agent_files | 项目说明专项：统一说明文件加载策略 |
| connector_cross_agent_sync | 连接器专项：显式配置同步服务 |
| connector_app_server | 连接器专项：正式注册服务与 RPC |
| app_server_recovery_contract | 阶段 5 / 8 审查恢复入口与生命周期后合入 service |
| code_block_terminal | 终端 UI 协议专项：显式注册协议转换 |
| live_steering_stream_contract | 阶段 5 / 8 合入流式服务事件处理，保留 superseded 临时项清理 |

这些专项未纳入本轮阶段 3 的“行为不变”合并。后续修改需各自证明生产入口和测试入口一致。

## 验证与发布

方法归属守护遍历实际生产类整个 MRO，禁止任何方法来自本阶段移除的四个补丁模块。
基础 service 和 Streaming service 构造前后均检查方法身份、提示 hook 与实际请求一致性。
生产类采样中断测试分别覆盖直接构造与 Streaming service 构造，使用伪 provider，
验证同一 turn、去重、持久新指令及旧采样取消；没有发送真实模型请求。

阶段 3 与后续阶段按源码提交、CI 验证推进；稳定 Host 仍需单独手动发布。
