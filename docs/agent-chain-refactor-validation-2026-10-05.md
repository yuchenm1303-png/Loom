# Agent 链路重构验证记录

此记录区分源码回归与真实模型表现。阶段 0–3 已单独提交；阶段 4–8 整合后统一验证。没有发送付费模型请求、修改用户会话或触发正式 Host 发布。

## 已实现范围与可复验测试

| 审计问题 | 源码处理 | 主要回归 |
| --- | --- | --- |
| 1.0.27 全会话插入资源释放消息 | 已移除无事实的前置提示；实际释放按资源事实记录 | resource_resume_contract / browser_session_lifecycle |
| 60s 静默、输出截断与额度重试 | 阶段 2 已按 provider 分离超时；截断优先识别；quota 不重试 | alignment_reliability / provider_streaming / stream_activity_contract |
| 多层请求 builder / service 再修改请求 | 单一 renderer、显式 context provider | context_composer / project_runtime_instructions / agent_request_layout |
| 前缀不断变化、未计缓存 | anchored frames，provider cached tokens 全链路传播 | cached_model_usage / context_frame_repair |
| 内置型号缺上下文声明 | endpoint/model 隔离声明，软工作预算和硬窗口分离 | working_context_limits / unknown_context_window_semantics |
| 浏览器每回合被销毁 | 会话租约、活动/执行保护、TTL 与容量回收 | browser_session_lifecycle / browser_session_reservations |
| 贴纸放大旁白、改写模型历史 | 展示投影与执行分开 | sticker_display_boundary / app_server_display_projection |
| 计划只有口头证据 | outcome、实际调用/文件引用、durable check ledger | evidence_contract / plan_progress_projection |
| _record 多层副作用 | 唯一 storage owner、正式生命周期 hooks | runtime_event_lifecycle / live_steering_interrupt / computer_runtime |
| 启动期恢复和流式替换 | 两个补丁并入 service 源码 | preintegration_app_server_recovery / recovery_handoff_contract |
| 休眠 Stop 审查 | 删除 reviewer 主路径和专属功能测试 | completion_protocol_architecture / turn_completion_lifecycle |
| 全量重复解析事件 | 有界增量 cache、返回值隔离 | event_parse_cache / recovery_storage_parity / recovery_handoff_contract |

## 红、绿、撤销验证

- 真实请求 composer 前缀和预算新回归通过；临时恢复前置最新 snapshot 布局后前缀守护失败，精确恢复后通过。
- 截断恢复上下文原先在预算后附加，新测试发现实际量与估算不一致；移入统一 renderer 后通过，撤销这条接线后再次失败。
- frames 修复前 3 项失败，修复后含 Host 重启/压缩的 6 项新测试通过。
- cached token 修复前 8 项失败；撤销 usage 字段后 12 项失败，恢复后通过。
- working budget 字段/集成回归修复前失败；撤销软预算后 3 项失败，恢复后通过。
- 浏览器租约初始 4 项失败，撤销 manager 后 8 项失败，恢复后通过。
- 贴纸执行与展示边界分别撤销后 2 项失败，恢复后通过。
- completed plan 无证据/结果拒绝；撤销检查后失败，恢复后通过。
- 事件 cache 撤销增量命中后解析记录 18 != 3，恢复后通过。
- Stop 构造签名与 request purpose 守护修复前失败，撤销删除/契约后失败，恢复后通过。

临时撤销始终保存并按原字节恢复改动；日志保存在本地 scratch，未将真实用户数据写入 Git。上述回归不代替真实 provider 复验。

## 旧测试变更依据

规范用户/工具历史断言保留；截断重试的旧 ASSISTANT prefill 改为具名 runtime USER 项，仍验证完整部分输出可见、工具恰好执行一次及未完成段不独立进入 canonical history。旧 request index 断言改为新的 named context 位置。fake provider 读取最后 canonical 消息，不把 named runtime USER 项误当新用户指令。浏览器旧的回合结束释放断言迁至 TTL/容量/显式释放测试，保留资源可以还给其他任务的目标。模拟正式事件通过 _emit_event，直接 _record 的测试只验证存储。扩展 HUD 的旧源码条件断言增加 finish_turn 例外，同时保持会话驱动/只作用于自己的 tab 的断言，并验证 finish_turn 不 detach CDP。

Reviewer 专属测试随已删除的产品功能移除；混合用例中的正常计划、语言、工具证据、审批、rollover 和持续采样仍保留。详细清单如下：

# Removal of the dormant semantic reviewer

The supplied stage-8 handoff explicitly authorizes deleting reviewer-specific
code and tests. `review_stop` had no production constructor references; only its
opt-in tests invoked it. Removing those tests records removal of the product
feature, rather than weakening current task, tool or browser acceptance checks.

Deleted `turn_stop.py` and `turn_continuation.py`; removed the TurnRunner branch
and constructor option. `tests/test_turn_stop.py` was wholly reviewer-specific;
its generic Scripted platform and normal runtime factory were preserved in
`tests/scripted_agent_platform.py`. Its removed cases were:

- `test_review_rolls_up_unbounded_history_without_claiming_success`: exercises the deleted assessment protocol/request/parser/retry/continuation rather than the retained turn protocol.
- `test_stop_is_candidate_and_review_continues_same_turn`: exercises the deleted assessment protocol/request/parser/retry/continuation rather than the retained turn protocol.
- `test_legitimate_turn_end_is_explicit_and_durable`: exercises the deleted assessment protocol/request/parser/retry/continuation rather than the retained turn protocol.
- `test_text_only_continuations_are_not_a_completion_or_retry_limit`: exercises the deleted assessment protocol/request/parser/retry/continuation rather than the retained turn protocol.
- `test_repeated_continuations_can_end_with_an_evidenced_incomplete_result`: exercises the deleted assessment protocol/request/parser/retry/continuation rather than the retained turn protocol.
- `test_cancel_after_repeated_continuations_stops_before_another_request`: exercises the deleted assessment protocol/request/parser/retry/continuation rather than the retained turn protocol.
- `test_explicit_model_budget_is_independent_of_semantic_continuation`: exercises the deleted assessment protocol/request/parser/retry/continuation rather than the retained turn protocol.
- `test_invalid_decision_is_not_a_completion_signal`: exercises the deleted assessment protocol/request/parser/retry/continuation rather than the retained turn protocol.
- `test_assessment_failure_preserves_answer_without_success_verdict`: exercises the deleted assessment protocol/request/parser/retry/continuation rather than the retained turn protocol.
- `test_successful_tool_is_not_proof_all_deliverables_are_done`: exercises the deleted assessment protocol/request/parser/retry/continuation rather than the retained turn protocol.
- `test_cancel_during_stop_review_cannot_commit_candidate`: exercises the deleted assessment protocol/request/parser/retry/continuation rather than the retained turn protocol.
- `test_goal_is_context_not_automatically_marked_complete`: exercises the deleted assessment protocol/request/parser/retry/continuation rather than the retained turn protocol.
- `test_new_input_during_stop_review_supersedes_candidate`: exercises the deleted assessment protocol/request/parser/retry/continuation rather than the retained turn protocol.
- `test_nonretryable_assessment_timeout_preserves_answer_without_more_requests`: exercises the deleted assessment protocol/request/parser/retry/continuation rather than the retained turn protocol.
- `test_review_samples_obey_model_step_budget`: exercises the deleted assessment protocol/request/parser/retry/continuation rather than the retained turn protocol.
- `test_invalid_assessment_retries_only_private_request_and_accounts_usage`: exercises the deleted assessment protocol/request/parser/retry/continuation rather than the retained turn protocol.
- `test_original_user_request_survives_lossy_prepared_context`: exercises the deleted assessment protocol/request/parser/retry/continuation rather than the retained turn protocol.
- `test_review_honors_authoritative_output_cap`: exercises the deleted assessment protocol/request/parser/retry/continuation rather than the retained turn protocol.
- `test_reasoning_only_assessment_usage_is_accounted_on_retry`: exercises the deleted assessment protocol/request/parser/retry/continuation rather than the retained turn protocol.
- `test_compatible_json_assessment_uses_same_validation`: exercises the deleted assessment protocol/request/parser/retry/continuation rather than the retained turn protocol.
- `test_json_compatibility_never_bypasses_validation`: exercises the deleted assessment protocol/request/parser/retry/continuation rather than the retained turn protocol.
- `test_schema_recovery_diagnostics_do_not_expose_rejected_values`: exercises the deleted assessment protocol/request/parser/retry/continuation rather than the retained turn protocol.
- `test_exhausted_schema_recovery_records_unavailable_check_safely`: exercises the deleted assessment protocol/request/parser/retry/continuation rather than the retained turn protocol.
- `test_json_continue_decision_cannot_complete_unfinished_work`: exercises the deleted assessment protocol/request/parser/retry/continuation rather than the retained turn protocol.

Mixed-file removals:

- `test_task_convergence`: semantic continuations across tool batches/approval,
  assessor exclusion of actor promises, assessment prior-turn rollup, late
  guidance during assessment, bounded assessment evidence, assessment image
  transport. Each depends on the removed reviewer. Ordinary durable plan,
  invalid plan, language and prompt migration cases remain.
- `test_execution_context_lifecycle`: only superseding previous assessment was
  removed. The real rollover/plan/exact-evidence test remains and now uses
  `end_turn=False`, the supported native continuation protocol.
- `test_turn_completion_lifecycle`: optional-check exceptions and malformed
  optional-check output cases were removed with that callback option. Long
  history, explicit continuation and empty continuation remain; completion
  metadata now asserts the reviewer fields are absent.
- Browser action evidence keeps every actor attribution/geometry/trust
  assertion. Only secondary-review context assertions were removed. The
  generic fake-provider import points to the preserved helper.

No unrelated lifecycle, parallel execution, image, authorization or tool-result
assertions were relaxed. Historical event enum values remain for log compatibility.

- `test_provider_streaming::test_private_stop_assessment_stream_never_reaches_public_subscribers`
  was specific to the deleted private-review request. It is removed with that
  request purpose; public delta, reasoning, opt-out, marker sanitization and
  cached-usage tests remain. New architecture tests assert `stop_review` cannot
  be constructed and no hidden callback/source files remain.
- `ChatRequest.purpose` now accepts only `generation`. The dormant string-based
  branch suppressing output has been removed at provider and runtime layers.
  `TURN_STOP_*` enums and old-event token accounting remain solely for historical
  log compatibility, not new execution.

## 最终整合验证

阶段 0–8 的源码工作已完成。最终提交的基线为远端 main `403e87ab`，仅应用本任务的 diff，未带入其他本地提交。

- 最终远端基线全量：**2186 passed, 6 skipped，0 warnings**，382.70 秒。命令使用 `-W error::pytest.PytestUnhandledThreadExceptionWarning`，没有忽略后台线程异常；对应本地记录 `scratch/remote-main-final.log` / `.xml`。
- 本地工作分支先前全量：2192 passed, 6 skipped；其中 Windows taskkill 解码警告随后单独修复，最终基线全量已覆盖修复。
- Windows 清理进程输出保持二进制，避免 Python UTF-8 mode 误解码本地 OEM 文本。真实取消测试已加入线程异常失败标记；红、绿、撤销再红、恢复通过。取消/PTY/沙箱组合 32 passed。
- 远端基线单独链路守护：44 passed。
- 同一远端基线前端：UFO/relay 62、Electron 59、streaming 20、portal 3，共 **144 passed**；typecheck、build、portal bundle verification 均通过。
- `compileall` 与 `git diff --check` 通过。

正式 Host 发布仍为手动流程。本提交不修改版本号、不触发安装包或 Host 发布；stable runtime 的真实模型复验仍在下一节清单中。GitHub CI 的最终状态以提交对应 Actions run 为准，不能用本地结果代称云端结果。

## 本地基准（伪 provider，无付费请求）

3000 个事件，关闭缓存与开启缓存对照：热读中位数 44.62 → 15.50 ms；真实 request_preparation_ms 三次为 [307,256,248] → [191,156,141]；累计 JSON 解析 80303 → 3284。它只代表本地该数据集，不保证真实模型时延改善。

## 真实运行复验清单

使用相同浏览器验收任务、明确记录 Host source/version、模型/endpoint，保留修复前后日志。先新会话，再长会话换模型继续；分别测成功、provider 429/超时、审批暂停恢复、闲置到 TTL 与 Host 重启。只使用授权的测试 tab。

比较终止原因分布、max_content_gap_ms 和 stall 数；每步输入/cached token；旁白数量/中位长度；计划 outcome 覆盖率、证据引用可解析率；回合边界前后 browser_id/revision 连续性。20 KB 工具参数请求须有 quota 且用户明确授权后再做，分块计时确认 MiniMax 是否批量发送参数。

仍需人工检查证据是否真的支持 verdict；引用可解析和通过验收是两件事。模型可能仍不及时更新计划或提前结束回合，不能用新的语义审查和关键词再掩盖。
