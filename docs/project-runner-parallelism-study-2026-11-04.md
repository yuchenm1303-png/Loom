# 建造模式并行升级研究（2026-11-04）

问题：建造模式（持续项目 / weave）现在最多 2 个 agent 并行。业界有几百上千个代理协作的系统，Loom 要不要、怎么往那个方向升级？

结论先说：**要升级，但扩展的单位不是「同时写代码的 worker 数」，而是「同时工作的代理总数」。**
- 实测里最大的耗时和 token 都在**审查**，而审查是串行的。
- 现有的 2 个 worker 大部分时间也没用满。
- 第一步应该让审查和探索能大规模并行，同时把固定上限换成按资源调度。
- 写代码的 worker 上限放到 4–8、按依赖关系图宽度自动决定，放在后面做。

## 1. 现状（源码核对）

| 限制 | 位置 | 内容 |
|---|---|---|
| worker 上限 2 | `app/project_runner/service.py` `project_run_create`（`1 <= workers <= 2`）；能力声明 `maxWorkers: 2`；`weave.py` `weave_propose` 的 `max_workers` 为 `enum [1,2]`；`desktop-react/src/components/projectOverview.ts` 写死 `maxWorkers: 2` | 三层都写死 |
| 全局单项目 | `parallel.py` `eligible()`：非团队 run 要求 `not active`；团队子 run 要求所有在跑 episode 同属一个 parent | 整个 Host 同时只跑一个项目 |
| 一次只开一个子 run | `parallel.advance()` 每轮最多 `spawn` 一个，`return True` 后本轮结束 | 调度节拍限制爬坡速度（每秒一轮，影响小） |
| 合并串行 | `parallel.aggregate()` 一次合一个 | 顺序合并 + 单独审查 |
| 整体审查单会话 | `runner.py` verifying 提示词：一个审查员跑完全部 criteria | 最大瓶颈（见第 2 节） |
| 项目内禁用通用子代理 | `delegation.py` guard | 项目 episode 里不能开 chat 那套子代理 |
| 对话模式的子代理 | `agent_runtime/agent_control.py`：并发执行 4、每棵树 16 个、深度 4 | 已经有一套能用的机制 |
| 单会话内工具并行 | `runtime.py` `max_parallel_tools` 默认 4，上限 16；工具需声明 `supports_parallel_tool_calls` | exec 等进程工具已声明可并行 |

审查续跑「从头重跑全部检查」的问题，已在 82bf52dc（11-01）用 `reviewProgress`/`remainingCriteria` 修了。下面 weave-live2 的数据是修复之前的，**修复后还没有用真实模型重测过**。

## 2. 实测数据

工具：`scripts/profile_project_runs.py`（只读）。数据来自 `.cache/*/state/project-runner/projects.sqlite3`，即 10-28 至 10-30 的真实模型资格测试。

- ep-min：episode 分钟，并行时会叠加。
- 并发分布：墙钟时间里同时有 0/1/2 个 episode 在跑的占比。

| run | 墙钟 | token | 写代码 | 单任务审查 | 整体审查 | 并发分布 0/1/2 |
|---|---|---|---|---|---|---|
| ledger12（5 任务） | 36 min | 4.8M | 34% | 53% | 13% | 2/62/37% |
| ledger11 | 53 min | 5.9M | 61% | 18% | 21% | 2/64/34% |
| ledger10 | 42 min | 5.9M | 44% | 22% | 35% | 2/88/10% |
| chaos-ledger1 | 51 min | 4.3M | 46% | 40% | 14% | 3/63/34% |
| weave-live1（5 个里程碑） | 433 min | 30.2M | 44% | 38% | 16% | 6/78/16% |
| weave-live2（15 任务） | 598 min | 42.4M | 36% | 9% | **55%** | 13/68/20% |

表里的百分比是 token 占比，耗时（ep-min）的分布和它接近。weave-live2 的整体审查共 16 个 episode、335 ep-min，**一个阶段就占了一半以上的墙钟时间**。

### 依赖关系图的宽度（planner 实际产出）

| run | 任务数 | 深度 | 每层宽度 |
|---|---|---|---|
| ledger 系列 | 4–5 | 3–4 | [1,1,1,2] 或 [2,1,1] |
| weave-live1 里程碑 1 | 12 | 5 | [1,3,5,1,2] |
| weave-live1 其余里程碑 | 4 | 4 | [1,1,1,1] |
| weave-live2 | 15 | 7 | [1,1,1,5,1,2,4] |

### 解读
1. **2 个 worker 都没用满。** 同时有 2 个 episode 在跑的时间只占 10–38%，60–88% 的时间只有 1 个。原因有三：
   - 依赖关系图多数层宽度为 1。
   - 每个子任务的流程是「写 → 审 → 合并」，合并和整体审查又是串行的。
   - 文件范围冲突会把可并行的任务挡住。
2. **审查才是大头。** 单任务审查加整体审查占 token 的 50–65%（weave-live2 为 64%）。
3. **提高写代码上限的收益，有一个粗略上限。** 只有宽度 ≥3 的层能受益：weave-live1 里程碑 1 的第 3 层（5 个任务）、weave-live2 的第 4 层和第 7 层。按 Amdahl 粗估，2→4 个 worker 能让写代码阶段快 15–30%，折合墙钟时间**大约快 10–15%**。这是推测，需要用第 4 节 P0 的指标核实。
4. **审查并行的收益更大。** 如果 weave-live2 的整体审查按任务拆成 4–5 个验证者并行，335 ep-min 中大部分可以重叠，**墙钟时间大约能省 35–45%**。这同样是推测；而且续跑问题已修，重测后基数会变小。

## 3. 业界做法（已读原文）

- **Anthropic 多代理研究系统**（[原文](https://www.anthropic.com/engineering/multi-agent-research-system)）
  - 主代理通常同时开 3–5 个子代理，复杂问题 10 个以上；子代理各自并行调用 3 个以上工具。复杂查询的耗时最多降 90%。
  - token 消耗约为普通对话的 15 倍。在 BrowseComp 上，token 用量一项就解释了 80% 的效果差异。
  - 原文明确说多数编码任务真正能并行的部分比研究少；需要共享上下文、或代理之间依赖多的领域不适合。
- **Cursor「Scaling long-running autonomous coding」**（[原文](https://cursor.com/blog/scaling-agents)）
  - 一个项目上几百个 worker 同时推送到同一分支，单次运行持续数天到数周，消耗「数万亿」token。
  - 两种协调方式失败过：加锁时，20 个代理的有效吞吐只相当于 2–3 个；乐观并发更简单也更稳，但没有层级时代理变得回避难题、不敢冒险。
  - 最终架构：planner（可以递归开 sub-planner）负责持续拆任务，worker 互不协调、只做自己的任务，judge 决定是否进入下一轮，每轮从头开始以对抗漂移。不同角色用不同模型。
- **Cognition「Multi-Agents: What's Actually Working」**（[原文](https://cognition.com/blog/multi-agents-working)，2026-04）
  - 实际有效的模式：只读子代理（搜索）；生成者加验证者（干净上下文的审查员能发现写代码者看不到的 bug）；manager 加子任务的 map-reduce-manage。
  - 并行写代码的群体至今没有实际落地，理由是「每个动作都隐含决策」。
  - 前作 [Don't Build Multi-Agents](https://cognition.com/blog/dont-build-multi-agents) 提出了同样的上下文共享原则。
- **Codex 源码**（openai/codex 48524f3，2026-11-04，`codex-rs/core/src/config/mod.rs`）
  - `DEFAULT_AGENT_MAX_THREADS = 6`，`DEFAULT_AGENT_MAX_DEPTH = 1`。
  - v2 版 `DEFAULT_MULTI_AGENT_V2_MAX_CONCURRENT_THREADS_PER_SESSION = 4`（含主线程）。
  - 也就是说，成熟的本地编码代理默认就是 3–6 个。

**对 Loom 的含义：** 真正规模化的部分是只读和验证。并行写代码要做到几百个，需要两个前提：一是云端算力和几乎不设上限的预算；二是 planner/worker/judge 的层级、worker 自己处理冲突、定期从头开始。Loom 跑在本机、花的是用户自己的 token，应该先把只读和验证这部分做成可扩展的，写代码的部分保持受依赖关系图约束的适度并行。

## 4. 升级方案（按收益/成本排序）

### P0 测量（小，先做）
- 每个项目 run 持续记录以下指标，在 hub 的项目概览里显示，并写进 `projectRun/overview`：
  - 各阶段的墙钟时间和 ep-min
  - 同时在跑的 episode 数分布
  - **有就绪任务、却被上限或文件范围挡住的时间**（`advance()` 里找到可做任务、但 `occupied >= maxWorkers` 或范围冲突时累计）
  - 被全局锁挡住的时间（`eligible()` 因别的项目在跑而返回 False 时累计）
- 改动：`parallel.py`、`runner.py`、`store.py`（加一张计数表）、`service.py` overview。
- 验证：soak 脚本（`scripts/soak_project_runner.py`）断言计数单调、和事件时间线一致。

### P1 整体审查拆分并行（收益最大）
- 进入 `phase=verifying` 且 `reviewScope=project` 后，不再开一个审查员，而是：
  1. 按任务（或每组 N 条 criteria）把 `criteria(run)` 切成若干分片。
  2. 每个分片开一个**验证者 episode**，各用一个冻结候选的只读 checkout。现有 `reviews/<run>--<candidate>` 只有一层；改为每个分片一个目录。
  3. 每个验证者只对自己那一片给出 verdict 和检查证据。证据复用现有的 `verification.case_id` 和接受检查的登记。
  4. 所有分片都给出 `passed` 后，再由一个**汇总审查员**做「整体目标覆盖」检查，只读各分片的结论和缺口，不重跑已经通过的检查。
  5. 任何分片给出 `needs_changes` 时，问题按 `taskId` 回到返工流程，规则与现在相同。
- 分片之间的资源冲突沿用 `sharedResources`：用到浏览器、端口的分片串行，其余并行。
- 预算：从项目的预算池里预留。验证者数量 = min(分片数, 资源许可数，见 P2)。
- 改动：`runner.py`（verifying 分支、提示词、`_review_result` 汇总）、`review.py`（分片候选、checkout）、`store.py`（分片状态）、`budget.py`（预留）、`janitor.py`（清理分片 checkout）。
- 风险：每个分片都要重新读一遍代码，token 可能增加 20–40%（prefix cache 能抵掉一部分）；汇总审查员可能漏掉跨任务的问题，所以汇总一步必须保留「整体目标覆盖」的判断。
- 验证：
  - 软测：分片切分、部分失败、Host 重启后恢复分片状态。
  - chaos：杀进程后不重复记账。
  - 真实模型：重跑 weave-live2 场景，对比墙钟时间、token 和预言器结果（21/21、45 条 criteria）。

### P2 按资源调度，去掉全局单项目锁
- 用一个准入控制器代替 `maxWorkers` 常量和 `eligible()` 里的全局 `not active`。同时在跑的 episode 数取以下几项的最小值：
  - 模型服务的并发许可（每个 provider 一个可配置值，默认 4；遇到 429 或 TTFT 恶化时自动收缩）
  - 剩余预算速率（已有 `budget.py` 的预留）
  - 磁盘：每个写代码 worker 一个 worktree，按仓库大小估算；C 盘低于阈值时不再开新的
  - CPU、内存（只做粗粒度检查）
- 每个项目再设一个上限（用户可调），不同项目之间公平轮转。
- 改动：新增 `admission.py`；修改 `parallel.eligible`、`runner.tick`；`service.py` 能力声明改为报告真实上限；前端 `projectOverview.ts` 不再写死 2。
- 风险：多个项目共用同一个仓库时，worktree 和 git 锁会争抢（`gitCommonDir` 相同就要串行化 git 写操作）。

### P3 写代码上限按依赖关系图宽度自动决定
- 先把 `maxWorkers` 放开到 1..8，默认「自动」= min(当前层可做任务数, 准入许可, 用户上限)。
- 现在要求「并行必须配独立审查」，这条保留。
- planner 提示词鼓励切出更宽、文件范围不重叠的依赖关系图，但不强求。数据表明大多数项目天然很窄。
- 改动：`service.py` 校验、`weave.py` schema、hub 的「+」按钮（`ProjectFlow.tsx`）、`program.py`。
- 预期收益：墙钟时间快 10–15%（见第 2 节，待 P0 数据核实）。

### P4 只读探索代理
- 规划之前，以及 worker 卡住的时候，允许开**只读**子代理：读代码、搜索、查文档，不写 checkout、不执行有副作用的命令，可以用便宜的模型。
- 实现：给 `delegation.py` 的 guard 开一个口子。只读子代理复用 `agent_runtime/agent_control.py`，权限固定为只读沙箱，token 计入项目预算（`delegation.reconcile` 已有导入用量的路径）。
- 风险：只读权限必须由沙箱强制，不能只靠提示词约束。

### P5 难点任务多方案择优
- 同一任务返工 ≥2 次后，开 2–3 个并行尝试，每个在自己的 worktree 里做，用已接受的检查加审查结论选出最好的一版，其余丢弃。
- 依赖 P2 的准入控制和 P1 的验证能力；token 成本高，默认关闭，按预算等级打开。

### P6（远期）云端执行
- 把只读和验证类的代理放到云端沙箱，本机只保留写代码的 worker 和合并。这一步才是真正「成百上千」的规模。

## 5. 不建议做的
- 直接把写代码的 worker 改成几十上百个。数据表明依赖关系图宽度通常 ≤5；文件范围冲突和串行合并会把多出来的 worker 卡在等待上（Cursor 加锁方案的教训：20 个只有 2–3 个的吞吐）。本机的磁盘和用户自己的预算也撑不住。
- 让 agent 在项目 episode 里自由 spawn 能写代码的子代理。这会绕过准入、隔离和独立审查，正是 `delegation.py` 要防的情况。

## 6. 建议顺序
P0 → P1 →（重测 weave-live2）→ P2 → P3 → P4 → P5。
每一步都用 P0 的指标和真实模型资格测试判断要不要继续。
