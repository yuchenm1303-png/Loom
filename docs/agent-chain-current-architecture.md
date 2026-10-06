# Agent 执行链路当前架构

更新：2026-10-05。阶段 0–8 的源码重构和离线回归记录见 `agent-chain-refactor-validation-2026-10-05.md`。真实 MiniMax 长任务尚未复验，不能据此声称模型效率已达标。源码推送不会自动发布 Host。

## 请求与规范历史

`runtime.py::_prepare_model_request` 是生产 MRO 中唯一请求入口；`context_composer.py` 是传输、自动压缩、人工压缩和预算投影共用的 renderer。最终请求只有一个稳定 SYSTEM 前缀（主提示与固定协议），随后是规范历史与按历史锚点追加的具名 USER 上下文。具名项明确属于 runtime，不是人的新授权。

规范 `session.messages` 只记录用户、模型、工具协议。项目说明、runtime 状态、计划、语言、记忆及工具观察使用独立 `request_context_frames`。状态变更追加新快照，不改写此前前缀；项目 service 显式注册贡献者，不替换请求方法。修复孤立工具历史时，identity/occurrence 锚点避免把快照插入未闭合的工具协议。旧数字锚点保留兼容，但已经受旧 Host 修复影响的位置无法精确恢复。

完整 DOM、桌面观察及图片只在 Host 内存；持久化记录摘要与指纹。Host 重启后明确显示观察不可用，需要重新观察，不能把历史页面当当前状态。checkpoint 保留最新上下文版本并回收旧 overlay。

每次 `model_requested` 记录实际渲染布局、各消息估算、工具 schema 摘要及预算。缓存依赖整个前缀、工具和 provider；换模型/权限、压缩、Host 重启后丢失视觉观察均可能失去缓存。`cached_input_tokens` 使用 provider 返回值，不凭前缀相等宣称真实命中。

## 上下文预算

硬窗口、输出预留和工作窗口分别声明。官方直连 MiniMax M3 使用 512K 硬窗口、128K 工作目标；官方 DeepSeek 指定型号使用其声明的窗口。代理路由不继承直连保证，模型发现缓存按 endpoint/model 隔离。未知模型没有虚构窗口或输出上限；用户/服务自己的明确声明优先。

工作窗口触发较早压缩，是软目标：固定协议和不可分割工具记录超出目标时仍可使用 provider 硬预算，不能因工作目标无法达到就停止任务。压缩请求、压缩后的请求与真实发送使用同一 renderer。模型切换重新捕获限制，不带入旧型号的限制。

## 回合生命周期

`TurnRunner` 根据结构化模型响应选择工具执行、继续采样或回合结束。没有工具且协议表示结束，会结束这一回合；这不等于证明整个用户任务完成。没有文本关键词完成判断、第二模型收尾审查或累计驳回次数停止逻辑。历史 Stop 事件与旧用量回放仅用于兼容，不能重新创建 reviewer。

`_record` 唯一归属 Core，只提交事件与通知；`_emit_event` 显式安排展示投影和生命周期 hook。模型请求创建流上下文，响应提交消费观察/清理流，终止 hook 隐藏前台控制痕迹、清理 Computer Use 和贴纸流。资源所有权直到 finally 才释放，关闭 steering inbox 不会漏掉资源 deactivation。目标用量仍在终止可见前入账，保持转向竞态约束。

service 的恢复入口及流式 superseded 清理由源码正式实现，移除对应两个 import patch。读会话不会根据持久 RUNNING 自行宣布崩溃；安全交接恢复需要明确 recoverTurnId。其余连接器、标题和产品集成不属于本轮执行链路拍平范围。

## 超时与重试

provider 采样与流式传输拥有超时/重试；工具执行拥有自己的取消与超时。连接活动和内容进展分别计时，事件保留 chunk/content gap。模型输出截断先按 finish reason 分类，再进行参数格式验证；恢复提示及未提交片段也在 renderer 捕获后计算预算，片段只存 Host overlay，不能在预算后偷偷附加；额度耗尽不会继续重试。没有更改真实 provider 的输出能力；一次性大工具参数仍需真实复验。

## 浏览器资源

浏览器按对话保留，成功、失败、取消、审批等待不再销毁 browser_id。默认闲置 20 分钟释放；活动回合、启动预留和执行中的操作受到保护。容量不足只回收其他无活动回合的最久闲置会话。显式关闭、权限变化、归档/删除和 Host 关闭拥有正式释放路径。对真实浏览器，回合结束只移除 HUD 等前台痕迹，保留逻辑 session。

每次逻辑 lease 开启/释放记录独立 durable event，不覆盖会话快照。Host 重启后未关闭的旧 lease 明确记录 host_restart；首采样只追加此前尚未报告的实际释放事实。单个上下文项展示最新 32 条收据并标明省略量，全部事实仍在 journal；失效 ID 的工具回执可查真实原因。无法恢复底层连接时必须重新连接，不得冒充仍然存活。

## 展示与证据

贴纸在最终展示投影中生成；不注入模型执行提示，不改变模型原文或 canonical 工具历史。service 的 text 为显示内容，rawText 保留原文。工具调用旁的原始模型说明不被运行时删减。

计划 completed 必须给 outcome 及可解析 evidence_refs。引用执行过的同会话 call_id 或 workspace 文件；not_executed 不能作为执行证据。`record_check` 将模型判断与运行时事实分开记录，时间差由引用事件计算；`read_check_ledger` 读取台账。运行时验证引用，不裁定 passed/failed 是否正确。UI 分开显示阶段完成与验收结论。

## 事件索引

`event_cache.py` 按会话增量解析 journal；默认 8 MB、16 个 LRU 条目。冷 recent 只读尾部。追加、正常恢复、截断、同尺寸重写、原子替换、不完整尾部有回归；返回值深复制，调用方不能污染缓存。绕过 journal 锁并在追加时偷偷改写遥远中间字节不在低成本增量检测保证内，正式写入仍必须使用 journal。

## 对照依据

固定 Codex 源码 [`session/mod.rs:4635`](https://github.com/openai/codex/blob/a7660cd15490875b8c22f66e577da115ed927fe3/codex-rs/core/src/session/mod.rs#L4635) 记录上下文 baseline 和后续变化；Loom 采用自身明确的投影/历史边界，不能把架构相似称为效果等同。

[OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching) 要求实际前缀匹配；[MiniMax M3](https://www.minimax.io/models/text/m3) 与 [DeepSeek 模型声明](https://api-docs.deepseek.com/api/list-models/) 支持直连元数据，不能保证代理窗口。
