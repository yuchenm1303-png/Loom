# 持久工作型 Agent：独立验收与自动返工

状态：第二批后端实现，继承 [第一批执行底座](project-runner-first-slice-2026-10-25.md)。尚未实现自动集成发布、多 worker、项目 UI 或长达数天的真实模型验证。

## 执行闭环

新项目默认 `reviewPolicy=independent`：规划 → 实现候选 → 当前任务独立验收 → 失败带反馈返工 / 通过后下一任务 → 整个项目重新验收。

验收失败不直接修改实现：reviewer 返回有 taskId 的具体问题，runner 持久保存反馈并交还 worker；修复后创建新快照和新 reviewer 会话。整个项目的复验若发现后续任务破坏了前面的功能，会重新打开相关任务及其顺序下游，最后再次整体复验。

同一任务或项目整体的负面验收/过期失效计数跨会话保存，达到 `maxReviewCycles` 后停止自动返工，状态为 needs_review。默认 3，允许 1..10；总 token/episode 上限仍生效。reviewer 可以 continue 跨轮完成检查，同一验收轮次允许引用前一轮会话 episode 中的检查；换候选后绝不继承旧 reviewer 的通过证据。

最终通过后仍停在 ready_for_review，并带 verificationStatus=passed、verifiedCandidate 和具体快照摘要。它表示该候选通过独立验收，不表示已经集成 main、推送或部署，更不表示整个重型功能已经完成。

## 源码与证据绑定

- 候选快照覆盖 Git 跟踪文件、未忽略的新文件、跟踪删除、权限位与 HEAD，并绑定目标、任务说明和验收定义的 hash。
- reviewer 用全新会话和独立 Git worktree，复制候选的未提交内容与删除，不直接在 worker 的目录里验收；同一 review scope 不复制 worker 的聊天历史。
- 复制前后、验收结果处理时均核对源码；定义变化、候选变化或 reviewer 改动受检源码会使结果过期，不能通过验收。
- passed 必须覆盖全部 criterionId，并由 runner 从当前 reviewer 会话找到真实 CHECK_RECORDED。可省 eventId，此时选择该 case 最新的运行记录；显式引用过早的 pass、外来 session 或失败记录均被拒绝。
- 只接受 runtime_exit_code / runtime_browser_assertions 的当前执行证据，且为 functional/visual 检查；普通 assistant_assessment、静态编译通过或单纯文字总结不能通过 gate。执行记录的 expectation 必须匹配被冻结的验收文字，subject snapshot 必须涉及真实候选源码。
- 任务分别通过后还要在新快照上重新跑所有必需检查，不能拼接不同版本的旧通过记录作为项目验收。

快照排除 Git 已忽略的文件：依赖、构建产物、测试输出应按仓库规则管理；它不是整盘或完整外部运行环境快照。不支持源码 symlink/submodule，单文件上限 32 MiB，总文件 16000、总字节 256 MiB；超出时明确阻塞，不静默截断。

worktree 是协作隔离，不是 OS 权限沙箱。reviewer 沿用用户已授权的工具权限，敏感执行仍可能等待正常审批；源码修改会在验收 gate 被拒绝，不承诺阻止 full-access shell 的所有副作用。检查退出码也不能证明测试本身设计正确，仍依赖独立 reviewer 判断需求覆盖和测试含义；本批没有把任意测试程序提升成形式化正确性证明。

## 持久化与恢复

项目 SQLite 添加 v2 的 project_candidates、project_assessments。源文件 manifest 单独存储，普通进度事件只引用摘要，避免每个事件重复复制大量文件列表。assessment 和任务状态变化在一次事务中保存，episode_id 唯一，重复导入不会重复返工。

重启后可以从原 reviewer session 的终结记录与检查事件导入结果，不重复调用工具；没有完成记录的操作仍按原有 outcome_unknown 规则处理。暂停、取消、审批与准备失败行为沿用第一批边界。

所有 reviewer/修复/最终复验 episode 进入同一用量统计。预算在最终复验前用尽时只标 budget_limited，不把单项 verified 自动提升为项目 passed。预算仍按 episode 之间检查，尚不是请求级硬预算。

## API 与兼容

initialize 的 projectRunner capability 为 version=2，声明 independentReview 与 automaticRework。

projectRun/create 新参数：reviewPolicy（默认 independent，可显式 manual 保留第一批人工验收流程）、maxReviewCycles（默认 3）。已有 v1 记录保持原行为，不因升级而自动开始新工作；遗漏新参数的旧创建命令重试保留原 fingerprint。

旧的人工候选、已停止且有明确结果的候选，或预算停在复验前的候选，可以显式调用：

```json
{"method":"projectRun/review","params":{"operationId":"unique-review-id","runId":"existing-run-id","expectedRevision":12,"maxEpisodes":30,"tokenBudget":100000,"maxReviewCycles":3}}
```

该命令冻结当前内容并启动整个项目复验；可显式补充总预算，保留已用量。它重置新一次人工发起的验收周期计数，操作本身保留审计记录。未知执行尚未核对、仍有 activeEpisode 的项目不能用它绕过恢复。重复同 operationId 和输入不会启动第二次复验。

projectRun/assessments 返回最近 100 条验收记录（含来源 session/turn、源码 digest、检查 eventId、问题与判定）。projectRun/get 增加 verificationFreshness：current / stale / unavailable / not_assessed；通过后再编辑文件会标 stale，历史 verifiedCandidate 不被改写。list/events 仍是历史状态投影，实时新鲜度以 get 为准。

## 验证范围

测试使用脚本化模型、真实 runtime、真实 Git worktree 与真实 Python 子进程检查，覆盖失败后修复、整体回归重开下游、逐项证据、静态/伪造/外来/旧通过记录拒绝、源码和定义变化、重启导入、跨 episode 验收、预算边界、旧候选显式复验、生产 Host 共存和内部会话不额外生成标题。未运行付费真实模型项目、安装包长时验收或七天 soak。

最终验证：在干净 origin/main（5438d7ca）上仅叠加本任务改动，项目验收、第一批执行、轻量常驻、生产 browser policy、App Server/协议及已有 durable goal/多 agent 恢复共 113 项通过，耗时 192.98 秒。保留并验证主分支已有的 project_run_create 命名修复，没有覆盖原 workspace project/create 接口。依赖使用 bundled Python 和已有测试依赖，未引入新运行库。
