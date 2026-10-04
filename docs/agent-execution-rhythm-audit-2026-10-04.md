# 执行节奏与上下文链路审计

本次依据实际失败日志和公开 Codex 源码核查运行链路。对照的是
`openai/codex@a7660cd15490875b8c22f66e577da115ed927fe3`（2026-09-29），
不是 Codex 桌面客户端的未公开实现。此前移除的收尾拒绝次数上限继续保持移除。

## 源码依据与适配边界

- [session/turn.rs](https://github.com/openai/codex/blob/a7660cd15490875b8c22f66e577da115ed927fe3/codex-rs/core/src/session/turn.rs)：工具/模型继续信号和待处理输入决定继续采样；没有后续工作信号时才运行 Stop hooks。阻止收尾的反馈进入上下文，然后继续同一轮。工具调用量本身不证明任务完成或应该重做审计。
- [tools/parallel.rs](https://github.com/openai/codex/blob/a7660cd15490875b8c22f66e577da115ed927fe3/codex-rs/core/src/tools/parallel.rs)：并行是工具能力声明，串行工具互斥。它不会把预先生成的浏览器索引自动修正成新的页面索引；Loom 的 revision 校验仍有必要。
- [tools/handlers/plan.rs](https://github.com/openai/codex/blob/a7660cd15490875b8c22f66e577da115ed927fe3/codex-rs/core/src/tools/handlers/plan.rs)、[gpt_5_2_prompt.md](https://github.com/openai/codex/blob/a7660cd15490875b8c22f66e577da115ed927fe3/codex-rs/core/gpt_5_2_prompt.md)：用结构化计划表达复杂任务的阶段；计划由模型维护，不能靠解析叙述来判定完成。Loom 的 evidence/blocker 字段是自身的证据约束，不能据此称实现与 Codex 完全相同。
- [tools/handlers/new_context_window.rs](https://github.com/openai/codex/blob/a7660cd15490875b8c22f66e577da115ed927fe3/codex-rs/core/src/tools/handlers/new_context_window.rs)：模型可以主动请求新上下文。上游工具的契约是不总结历史；Loom 使用 Chat Completions 和已有检查点服务，因此本次采用总结、归档再继续的适配方式，不声称是逐行移植。
- [context_manager/history.rs](https://github.com/openai/codex/blob/a7660cd15490875b8c22f66e577da115ed927fe3/codex-rs/core/src/context_manager/history.rs)：模型窗口与持久记录的用途不同。精简窗口不应伪造用户意图、工具执行结果或调用身份。

## 确认的问题及修改

| 已确认的问题 | 修改后的行为 |
| --- | --- |
| 同一批依赖页面状态的动作携带同一旧 revision；第一个动作完成后，其余过期。旧恢复又刷新页面，继续改变 revision。 | Host 用类型化冲突返回已有最新观察，标明 `execution_status=not_executed` 和 `error_code=stale_observation`。不派发过期动作、不改写提交参数、不自动重试索引。模型从结果选择下一步。共享观察契约说明依赖动作需要逐步选择，而不是重复扩充每个工具的 schema。 |
| 模型消费观察时清掉整个反馈对象，也丢掉动作比较基准。下一步经常被当成初次观察。 | DOM/图片依然在消费后过期；每个浏览器只保留比较摘要，区分 changed、uncertain 和初次 observed。`click_at`、`send_text` 也按可能改变页面的动作判断。摘要不会作为页面内容进入模型历史。 |
| 新 DOM 按工具名前缀附到最后一条浏览器结果，可能附到另一浏览器的失败调用。 | 记录来源 call ID，并将观察附到准确的工具结果。原调用不在窗口中时才使用已有的、明确标注为外部数据的恢复附件。 |
| Stop 的剩余工作仅在下一次采样临时传入，执行一批工具后消失。 | 从持久事件投影最新评估，跨工具批次、审批和压缩保留。明确标明它是评估时的状态，后续真实结果可以满足它；不把模型评估提升为新用户要求。新用户输入或终态使旧评估失效。 |
| 基础运行时有计划回填，但生产 ContextAgentRuntime 覆盖请求构建器，绕过了它。 | 基础和上下文管理路径使用同一个执行状态服务；计划和评估进入实际模型窗口，包括压缩后的窗口。测试覆盖 ContextAgentRuntime，而非仅测试基础类。 |
| 按 4/8/16 等调用数插入重新检查、收敛提醒；提示词也按调用数要求进度。 | 移除调用量检查点；仅保留实际重复操作的证据提醒。默认提示词 v9 要求围绕有意义的结果、阶段变化和阻碍简短更新，常规回执不逐条复述。旧默认提示词迁移，用户自定义提示词保留。 |
| 实际长会话累积大量历史；模型未声明容量，不能诚实推导自动压缩阈值。 | 增加模型可调用的 `new_context`，在下一次采样前复用已有总结和归档服务；请求是持久事件，成功检查点后不会再次触发。已知模型窗口仍按原策略压缩，未知窗口不新增猜测阈值。 |

## 验证与可判定范围

回归场景包括：同 revision 的三动作批次，过期动作不触达后端且不额外刷新，随后使用真实返回 revision 完成操作；跨模型步骤的效果比较；不同浏览器的观察归属；跨批次继续、模型主动压缩、原始证据归档与计划回填；新用户要求覆盖旧评估；旧默认提示词迁移与自定义提示词保留。

验证结果：主要相关回归 688 项通过，1 项默认关闭的真实浏览器检查跳过；随后启用该检查，在独立的无头 Edge 中通过。另有 50 项补充检查通过，覆盖全部旧默认提示词迁移、工具发现和并行执行等路径。Windows 子进程检查使用 `PYTHONUTF8=1` 保持父子进程输出编码一致。

这些测试验证运行时不丢状态、不串证据、不误执行过期动作、不因工具调用量强迫重规划。它们不证明某个模型一定遵守表达要求，也不证明长任务时间或 token 使用已经下降。真实模型复验应固定同一任务与后端，比较实际完成的阶段、进度消息数量和长度、过期调用、重复动作以及总耗时；不能只看 UI 是否折叠了旧消息。

本次修改属于 Host 的 agent runtime。提交代码不会更新已经运行的旧 Host；前端刷新也不能替换它。部署使用新源码的 Host 后，已有默认提示词会在会话加载时迁移。
