# 持久工作型 Agent：首版实施规格

日期：2026-10-25。状态：设计冻结候选；本轮不接入生产 runtime、不启动自主项目、不改变现有聊天行为。前置研究见 [架构研究](long-running-project-architecture-2026-10-24.md)。本文决定首版怎样实现，并明确实现前需要补齐的协议。

## 1. 产品契约

用户提交一次目标并启动项目。Loom 自主形成规格和验收、推进纵向切片、迭代修复、保存工作，跨会话和 Host 重启继续。用户可以中途问进度、改方向、暂停、恢复和取消。项目只有在最终集成版本满足必需验收并完成已授权交付后才完成。

「一句话」是允许的输入，不要求用户填工程表单。创建后的第一步是 planner 提出可执行规格、假设与范围。普通可逆选择直接作出并记录；无法合理推断的关键需求才请求决策，其他独立工作继续。启动必须带明确预算政策：金额/token/时间边界，或用户明确选择不限；不能擅自把一次普通问答变成无限期耗费项目。

持续运行以主机可用为前提。睡眠期间不执行；注销后的 GUI 不保证可用；恢复后依据实际环境核对。无需保持一个模型请求运行一周。

## 2. 首版范围与命名

以下是产品级首版，不等同第一批代码。先以串行实现验证同一套数据协议，随后将实现并发上限从一开放到二；不另做一次性串行架构。

| 概念 | 定义 | 终止含义 |
| --- | --- | --- |
| project | 用户授权的长期目标，绑定一个已有 Loom workspace project | 必需验收及交付全部满足 |
| task | 有版本、依赖、输入输出和验收的工作契约 | 结果已经集成且验收有效 |
| attempt | 某 task 的一次实现/修复尝试，固定 base、工作目录和 epoch | 候选提交成功，或本次尝试失败/取消/结果未知 |
| episode | 一次有界工作会话，映射一个真实 runtime session/turn | 本次会话结束，可继续同一 attempt |
| assessment | 针对确定代码、定义和环境的验收记录 | passed/failed/blocked，不改变代码 |

规划、诊断和最终验收也是可恢复工作：以显式 kind 的内部 task 建模（planning/implementation/diagnosis/verification/integration），统一使用 attempt/episode，不产生没有归属的后台调用。内部 task 不进入产品功能完成率；它们的完成谓词按角色定义，例如 planning 以规格成功持久化为完成，而不是以候选 Git 提交为完成。实现 attempt 的 candidate 是本次实现尝试的终态，task 仍等待验收和集成。

首版支持一个 Git 仓库、本地 Host、现有模型/工具、多次 episode、最多二个实现 worker、独立验收、串行集成、恢复、预算、可操作 UI。暂不支持跨仓库原子交付、多机调度、任意 agent 自建团队，也不要求自主生产部署或自身版本激活。

非 Git 项目首版返回明确能力限制，不暗中执行 git init。需要新增凭证、付费资源或外部发布时沿用现有授权边界。普通项目自动化不要求一律新增人工审批。

## 3. 与现有实现的准确边界

2026-10-25 读取到的轻量代码尚在另一个窗口修改，以下是观察而非稳定 API：

- `app/ambient.py`：独立 ambient.sqlite3，定时/文件/事件触发，资源名租约；重启将未完成运行设 needs_review 并禁用相关触发器。
- `app/ambient_service.py`：ServiceExecutor 调用现有 turn_start，轮询同一 turn 的结果；AmbientServiceMixin 用 service._guard 串行准入。
- `app/app_server.py::turn_start`：生成随机 turn_id，异步准备凭证再运行；尚无重型 operation_id 幂等创建协议，准备失败可能尚未有 TURN_STARTED。
- runtime 的 create_session 接受 workspace_dir；AgentControl.spawn 默认继承父目录，不能直接用来并行创建隔离 worker。
- AgentLimits 和 context reserve 不是项目预算；现有 durable goal 在 turn 结束后累计用量，不能当并发模型请求的硬预算闸门。

结论：重型模块复用 Host、现有工具循环与准入；不复用 AmbientScheduler 的业务队列，也不修改它的保守恢复规则。不将 prompt 前缀当成可信 project_id、权限或用量归属。

## 4. 模块与存储选择

计划新增以下模块，名字为候选，尚不存在：

| 模块 | 唯一职责 |
| --- | --- |
| app/project_runner/store.py | 迁移、事务、命令幂等、任务/attempt/episode、事件及 outbox |
| app/project_runner/service.py | 用户命令、查询、状态投影、控制版本 |
| app/project_runner/scheduler.py | 依赖与公平调度、重试、收敛/完成 gate |
| app/project_runner/episode_gateway.py | 运行归属、幂等派发、runtime 结果关联、取消与恢复 |
| app/project_runner/workspaces.py | worktree、源码快照、端口/目录隔离、候选提交 |
| app/project_runner/acceptance.py | 验收定义、证据、新鲜度、整体完成检查 |
| app/project_runner/integration.py | 单写集成、回归、main/push 事实核对 |
| app/project_runner/usage.py | 调用预留、全项目核算、未知用量与额度 |
| app/project_runner/recovery.py | Host 重启与 outcome_unknown 的事实核对 |

选择独立的 `projects.sqlite3`，复用 KeptConnection 的连接模式但显式 `BEGIN IMMEDIATE`，短事务且不持锁运行工具。比研究稿倾向同库更具体：项目层自己所有的状态、命令、预留和 outbox 在同库原子提交；会话日志和 ambient 账本是外部系统，通过幂等消息核对，不宣称跨库事务。数据库实际路径跟随 Host 数据目录，不放在代码仓库。

设计用 [SQL 草案](long-running-project-schema-v1.sql)只用于校验核心结构和约束，不是生产迁移。实现时用 project_runner_schema_versions 管版本，不修改其他模块的 user_version。Project Service 写入必须经过持久命令层；后台、UI 和模型都不能绕过它直接 UPDATE。

## 5. 状态与不变量

Project execution state：created、planning、running、verifying、completed、failed、cancelled。

与之正交的 control state：active、paused、budget_limited；实际暂停原因和可恢复条件单独记录。恢复过程用 recovering 标记表示，不能覆盖用户 paused。等待外部/决策作为结构化 blocker；若其他任务可运行，项目仍 running。UI 同时展示两类状态，避免一个失败 task 把全项目错误标成暂停。

Task：pending → ready → running → candidate → reviewing → integrating → done；修复回到 ready 并新建 attempt；依赖/外部问题 blocked；不可恢复 failed；范围淘汰 superseded（必需需求必须有替代任务，不能漏掉）。

Episode：prepared → dispatching → starting → running → waiting_approval / ended / failed / interrupted / outcome_unknown。waiting_approval 保持占用 session 执行权，但可释放模型槽位；审批可能持续很久，不按普通工作超时自动重试。一次 turn end 只更新 episode。

必须始终成立：

1. 一个 task 同时至多一个非终结 attempt；一个 session 同时至多一个活跃 episode。
2. 所有结果携带 project_id/task_id/attempt_id/episode_id、task_revision、spec_revision、control_revision、epoch；代码校验，不信模型填写的身份。
3. 旧 epoch 结果可归档，不能推进 task；control 变更后的结果需核对，不无声适配。
4. readiness 来自依赖的当前 done 结果；计划 DAG 无环、依赖同项目，验证由命令层负责。
5. 实现者只提交 candidate，不能直接写 done/completed；检查失败不因更换会话、改摘要或达到限额自动变通过。
6. 预算不足停止新模型调用；用户暂停/取消在重启后仍然有效。

## 6. 用户协议与进度界面

新增 `projectRun/*` 命名空间，避免和已有 workspace 项目 API 冲突。创建 operation_id 重试必须返回同一 run；同 key 不同输入返回冲突。所有变更请求带 operationId 和 expectedRevision，乱序请求不能覆盖新状态。

| API 候选 | 参数与语义 |
| --- | --- |
| projectRun/create | workspaceProjectId、objective、budgetPolicy、deliveryPolicy；只创建，不隐含执行 |
| projectRun/start | runId、expectedRevision；用户确认启动意图后持续工作，普通对话明确提出长期执行也可直接调用 |
| projectRun/get / list | 状态、验收覆盖、当前任务、blockers、用量、下次动作、结果位置 |
| projectRun/events | runId、afterSequence、limit；持久游标，UI 断线可补齐 |
| projectRun/steer | 用户新指令，生成新 control/spec revision，保留旧证据与决策 |
| projectRun/pause / resume | 停止/恢复新派发；pause 尽快在下一工具或模型边界退出 episode |
| projectRun/cancel | 先持久化 cancelled，再取消全部相关执行；没有再次自动启动 |
| projectRun/retry | 针对已核对的失败原因；未知副作用不能用通用 retry 跳过 |

项目页有目标、当前里程碑、任务与验收、运行记录、用量/预算、阻塞与操作。优先展示「已验收 6/10 项，集成 4 项」；不把 agent 估计百分比冒充确定进度。会话是工作证据的子入口；用户在项目页改目标，避免直接给 worker 私发指令绕过项目控制。

通知仅在里程碑、完成、失败或需要用户时产生；中间模型输出可展开查看。暂停按钮要显示「正在停止」直至进程已核对，不虚报已停。

## 7. 调度算法与执行协议

代码定时器或事件唤醒都可以调用 tick；tick 无模型调用，多个项目按轮转与 ready_at 排队；优先保护已预留的最终验收资源，不能由一个项目独占 Host。

```text
tick:
  导入尚未消费的 worker/工具终结事实（按 inbox key 去重）
  核对过期、被取消及准备失败的 episode
  对每个 active 项目：
    若规格未建立 → 派发有界 planning episode
    若有待集成候选 → 尝试领取仓库集成资源
    计算当前版本 ready tasks，不依赖模型自报
    原子创建 attempt、预留资源意图、写 episode/outbox
    事务外申请 Host 执行资源，二次检查 control revision
    若派发忙 → 有界退避；不计作实现失败
    若所有必需任务已集成 → 派发最终验收
    若最终 gate 通过 → 记录交付事实与 completed
```

资源跨库领取无法原子：先在项目事务写 acquire intent，事务外调用 Host broker，回来用 CAS 确认，失败则按 operation_id 补偿释放。取消和崩溃都核对 acquisition/release intent。锁顺序不能形成循环：不持项目 DB 事务去调用 Host；不持 ambient.guard 调用 Project Service；异步完成事件在 service._guard 之外投递项目层。

幂等派发需要新增一个窄的 Host gateway：先持久映射 operation_id→session_id/turn_id，再调用可接受预分配 turn_id 的内部派发函数。turn_start 本身不支持传入任意 turn_id，不应为外部客户端开放任意覆写；重型协议是受信服务内部接口。若崩溃发生在派发前/后，查询映射及准备/会话事实再核对，不能凭文本重新创建一次 turn。

准备阶段也必须有持久结果（preparation_failed / cancelled_before_start）；不能仅靠 renderer 的 turn/completed 通知。存在映射但没有 TURN_STARTED 不证明未执行，因为可能正在准备；先核对进程和 preparing 状态。

## 8. 交班、上下文与协作

同一 attempt 的普通 episode 继续当前 session，达到上下文/质量阈值后创建新 session 并保留 attempt 与 workspace。换 session 不增加 attempt 重试次数，也不清除失败历史。新 episode 的 work packet 包含当前规格、契约、代码版本、已确认检查、失败签名、外部操作未决状态与下一步建议。

模型可以提出 continue、candidate_ready、blocked、needs_replan；这些是建议，程序结合 Git 与证据决定实际状态。模型未提供建议、意外结束或输出格式错误时，先核对已持久化结果，再允许一次受限恢复；不能无限重复「请继续」。

负责人仅规划与重规划；worker 不能递归放大团队。第一次计划先建立可运行纵向切片、公共接口和少量里程碑，执行时滚动细化。计划变更附原因、影响闭包与 requirement 映射；删除任务不能删除必需需求。

## 9. 工作区与集成协议

每个 attempt 一个 worktree，所有 workspace/cwd/sandbox/AGENTS 都从真实目录绑定；先继承有效权限，再按该目录加载项目指令。只读验收使用候选 checkout；需要生成测试产物时使用 verifier 独立目录。GUI、浏览器、端口、DB 和依赖环境都需资源策略，worktree 不提供这些隔离。

公共接口、锁文件、migration 等冲突频繁的任务串行。集成只允许一个作者持 repo integration 资源。仓库级发布规则照实际 AGENTS 执行；本仓库要求完成验证后 commit main 并 push origin。worker 中间分支不是正式交付，集成发布者只提交本项目文件。

首版最保守的新鲜度策略：验收绑定精确 tree/commit、规格/定义 hash 和环境 fingerprint；任何相关输入改变均重验，不靠模型声称无影响。以后才加入有证据的局部依赖失效优化。build 产物和不影响源码的临时文件不应造成无限验收循环，快照范围显式定义。

合并冲突返回带冲突事实的修复 task，不允许静默 ours/theirs。推送失败先看远端 ref，已包含提交则补记成功，否则重新集成；从不 force push。UI 展示候选、集成和发布版本，三者不能混用。

## 10. 恢复与停止协议

| 事实 | 可自动动作 | 不允许的推断 |
| --- | --- | --- |
| outbox 尚未派发 | 同 operation_id 派发 | 新建重复 attempt |
| 有终结事件，项目未导入 | 导入原事件 | 再做一次实现 |
| 文件修改仍在，进程已确认结束 | 在保留目录创建恢复 episode | reset/clean 丢工作 |
| 进程可能仍活着、租约过期 | 限制发布，核对 owner，隔离旧目录 | 把 lease expiry 当死亡证明 |
| 非幂等操作结果未知 | 查询外部事实或 needs_input | 因没回执就重试 |
| 正在等待审批 | 展示实际审批状态；重启后走当前恢复边界 | 复用旧审批绑定自动执行 |
| 用户暂停 | 保存控制状态，停止新派发，安全退出当前工作 | 重启自动 resume |
| 取消后迟到结果 | 只归档，释放已确认资源 | 反转取消或自动发布 |

所有状态写入带 epoch/control CAS。允许长工作任务定期发送 code heartbeat，但 heartbeat 不等于验收进展。墙钟回拨不能抢占活跃 worker：进程内超时用 monotonic；重启后的持久 lease 仅触发核对。默认不在进程未确认停止时复用同一个 workspace。

## 11. 预算、失败与质量

预算 preflight 必须接入真正模型采样点，覆盖 worker、planner、verifier、stop check、压缩与记忆等归属到本项目的调用，并记录 provider SDK 的透明重试。不能只在 turn_end 后取 session 差值冒充硬预算。内部请求需带受信 project attribution，不从模型文本解析。

token 可以硬限制已知上界的请求总额度；输入 tokenizer/隐藏用量/透明重试上界不可信时只能称软预算或拒绝进入用户要求的严格模式。金额在价格与用量可信时计量，否则标估算。回执丢失保留 outstanding reservation/unknown，不按零释放。初值保留 20% 给集成与验收，比例可配置；未用余额回池。

实现默认建议：同一无新证据错误连续三次触发诊断；一个 task 至多三次普通修复尝试，然后负责人重规划或标阻塞；这些是实验起点，不能绕过总预算。没有代码 diff 不一定没有进展，新增复现/测量也是有效证据。相同错误重复、验收无推进、契约矛盾分别处理。

最终 gate 覆盖全部必需 requirement 的当前验收、关键用户流程、可复现启动/构建、交付位置和授权发布结果。Verifier 的意见也会错：保存命令、断言和观察，承认视觉/主观质量限制，不把第二个模型等同真值。

## 12. 实施批次与必须验证的行为

| 批次 | 代码范围 | 放行条件 |
| --- | --- | --- |
| P0 | store/schema、commands/events、只读查询投影 | 幂等命令、版本冲突、唯一活跃 attempt/session、原子 outbox；不调用真实模型 |
| P1 | gateway、单 worker、work packet、候选与验证、API/UI | 一句话形成规格；多 episode 完成纵向切片；关闭 UI 不中断；没有真实验收不能结项 |
| P2 | 恢复/取消/预算 hooks | kill 注入、准备失败、未知副作用、预算边界、旧 epoch、审批和重启暂停均正确 |
| P3 | 二 worker、隔离环境、串行集成 | 同 base 并行与落后 base、公共文件冲突、迟到结果、资源公平与预算竞争 |
| P4 | 长时真实项目、候选自维护 | 2h→24h→7d 真实运行记录；没有拿模拟时钟替代 soak |

P0 必须测试真实多连接领取竞争，不只 mock；P1 用确定性 fake model 加真实 runtime 跑通协议；P2 在提交边界强制终止进程而不是只 raise；P3 在真实 Git 仓库制造冲突与远端变化。真实模型运行单独计费且有上限。

暂不选择某一框架替换 runtime；先测出串行收益，再与受限多 agent 对比相同规格/模型/预算的验收通过、人工介入、成本与耗时。模型/角色数量是可调策略，持久状态与验收协议保持稳定。

## 13. 完成这轮设计后的边界

这一轮交付实施规格与可执行 SQL 草案，不宣称重型功能已经运行。轻量常驻由另一个聊天实现；其接口仍需在该任务提交后固定版本。下一轮实现从 P0/P1 开始，不先做七天自升级或全面重写运行时。

设计验证：使用 bundled Python 3.12 标准库 sqlite3 在临时数据库执行完整 SQL，验证了同项目依赖、禁止自依赖、唯一活跃 attempt、未知结果阻止自动替换、唯一活跃 session episode、operation_id 唯一、同 attempt 多 episode 接续、结算必须有真实用量、JSON 校验、状态/outbox 同事务回滚、两个真实连接竞争领取与旧版本 CAS、外键/数据库完整性，全部通过。该验证仅证明草案约束与示例事务可执行，不覆盖生产调度、工具恢复或真实模型。本次没有运行 pytest，也没有用此结果替代前一轮未跑通的 runtime 回归。
