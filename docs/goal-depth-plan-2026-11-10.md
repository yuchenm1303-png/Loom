# 长目标深度改进方案（2026-11-10）

依据：长目标 262978b8（E:\ai run，LLR 温度标定，DeepSeek V4.1 Flash，6 h）的完整运行记录，
加上对现有成熟研究型 agent 系统的调研。目标是让"一句话 → 领域水准成果"从"流程守规矩"走到"研究有判断"。

## 0. 结论

可靠性这一层（章程、账本、子线、独立核验、配额/断网恢复）已经接近或领先公开系统；**差距在研究判断与证据规模**：
不选题、不早审、有算力不加证据、没有客观评测。成熟系统里真正起作用的都不是提示词，而是**Host 强制的结构**
（阶段出口条件、评估器、锦标赛、复制节点、世界模型）——这和我们自己的门禁审计结论一致：纯提示规则大多被忽略。
所以方案只做三类事：修 4 个流程缺陷；把"选题、早审、加证据"三件研究判断变成 Host 驱动的步骤；建评测集来衡量。

## 1. 本次记录暴露的问题（证据）

### 流程缺陷
| # | 现象 | 证据 | 位置 |
|---|---|---|---|
| A | 断网后主线闲置 36 分钟 | 09:39 主线回合失败；子线 1 分钟后自动重试；主线直到 10:15 人说"继续吧" | `goal._advance`：`goal_lines.live(record)` 时不续主线，不区分"主动等子线"与"做到一半挂了" |
| B | 长目标中回合结尾向人提问 | 11:42:05 "要我接着往下推进哪一条？"，1 秒后目标回合自动开始 | `weave.py` 只给一行 "A long goal is running"，没说不要提问 |
| C | 推理泄漏成最终回复 | 第 1 轮干净结尾被验收门禁打回 3 次（`calib_variance.py` 未检），第 3 次输出 "But I must end turn… Correct behavior:…" | `turn_runner.py` finish_review；耗尽后取最后一次文字 |
| D | "0/11 已达标"读成失败 | 标准只在核验后翻转，checks=0 | hub/phone 章程渲染 |

另：GPU 在人提醒后才被发现，装 CUDA torch 用了约 1.5 h；`update_plan`/`record_check` 证据引用错误 7 次（一次重试即过）。

### 深度缺口（对照审稿人）
1. **未选题**：目标列了 3 个候选方向，lead 直接沿用前几次目标的课题（稿件在 11/9 已是同题），没有比较。章程 GUIDE 里已经写了 "pilot two or three candidate directions"——提示存在，没被执行。
2. **新意薄**：文献线自己找到 GMI 最优缩放与专利先例并收窄主张；标题级的"命题 1"是符号不变性，且"译码后 BER 不变"一句是错的。
3. **证据规模小**：1 个 checkpoint、1 个种子、QPSK、每点 512 时隙；局限里自己写"应推到 10⁴"，GPU 闲着没做。
4. **无替代方法对比**：训练端改损失、按比特标定、译码端归一化等均未比较。
5. **审稿只在末尾**：3 小时里没有任何外部视角；方向性问题（2、4）到核验时才可能被发现，改起来最贵。
6. **无客观对照**：没有任何基准可以说"比 Codex / Claude Code 做得好"。

## 2. 成熟系统怎么做（只列与上面缺口相关的机制）

| 系统 | 关键机制 | 对应缺口 | 备注 |
|---|---|---|---|
| Sakana AI Scientist v2（2504.08066） | 实验管理器分阶段树搜索，节点分 buggy/non-buggy/**replication/ablation**；VLM 审图 | 3 | 3 篇投稿 1 篇过 ICLR workshop；批评：实验有限、图误导——与我们同级 |
| Google AI co-scientist（2025，2026 Nature） | 生成→反思→**Elo 锦标赛两两比较**→进化；Proximity 去重 | 1 | Elo 随算力上升；是自评，不等于真值 |
| Kosmos / Edison（2511.02824） | **结构化世界模型**连接数据分析 agent 与文献 agent；200 次 rollout；每条结论链接到代码或文献 | 1、2、5 | 专家验证 79.4% 主张成立 |
| DeepScientist（2509.26603，ICLR 2026） | 发现=目标驱动贝叶斯优化；**Findings Memory**；漏斗 5000 想法→1100 验证→21 创新 | 1、3 | 瓶颈是实现与验证成功率，故过滤与验证最关键 |
| Meta AIRA-dojo（2507.02554） | agent=搜索策略+算子；**算子质量比搜索策略重要**（MCTS/进化对原算子无益） | 方案取舍 | 不必上树搜索，先把算子做强 |
| Jr. AI Scientist（2511.04583） | 从导师的基线论文出发：找局限→提改进→实验→写作；附风险报告 | 1 | 正是我们"在已有工作上继续"的形态；它要求先显式写出基线的局限 |
| Si/Hashimoto/Yang 执行差距（2506.20803） | LLM 想法执行前评分高、执行后掉得更多 | 1、5 | 只评想法不够，要小试点后再定 |
| Agents4Science 2025（2511.15534） | 315 篇投稿，AI 审稿有**谄媚**；人类评价"正确但不有趣不重要" | 5 | 审稿用异模型、先列拒稿理由 |
| Stanford Agentic Reviewer | 审稿前按多粒度自行检索 arXiv；与人类相关 0.42（人-人 0.41） | 2、5 | 审稿人自己做文献落地 |
| Luo/Kasirzadeh/Shah（2509.08713） | 基准挑选、泄漏、指标误用、事后挑选；**看日志+代码比只看论文更能发现** | 已做 | 我们的 _trace 已覆盖 |
| AlphaEvolve | 一切围绕**可编程评估器** | 3、6 | 评测集与加证据都要有程序化分数 |
| Anthropic 长时 harness / Codex /goal | JSON 功能清单只翻 pass；完成须有证据；暂停/恢复/预算 | D | 我们已具备；差在"自评 vs 核验"两层显示 |

## 3. 方案

原则：不加新提示规则；每一项都是 Host 能检查、能拒绝、能计数的结构，复用现有的 charter / lines / review round / claims / checker 基础设施。

### P0　流程缺陷（约 1 天，先做）
- **A 主线失败即重试**：`_settle_turn` 记下"上一回合失败"（`record["leadFailed"]`）；`_advance` 第 792 行在 `goal_lines.live` 时，若 `leadFailed` 且 `retryAt` 已过，仍 `_continue`（用 RETRY_NOTE 语义）。验收：脚本化模型复现"子线运行中主线 transport 失败"，主线 ≤2 min 内重启。
- **B 目标运行时不提问**：`weave.py` 的 long-goal 行加一句"不要以提问结尾：目标会自动继续；需要人决定时用 mark_thread_goal blocked"。另在 hub/phone 回合尾显示"长目标将自动继续"。验收：回放本次 11:42 回合的上下文，脚本检查最终文字不含问句（真实模型抽样 5 次）。
- **C 门禁与泄漏**：finish_review 在 (a) 目标有活动子线或 (b) 本回合有仍在运行的后台进程时不打回，改为在目标记录里留"待检"；耗尽时最终显示文字取被打回前最后一次干净的 `stop` 文本，而不是第 3 次。验收：本次 ee5c8fa7 的事件回放，最终显示为 "两条并行线…正在运行…" 那句。
- **D 两层状态**：章程标准显示三态——`待核验`（默认）、`lead 自评已满足`（lead 通过 weave_claims/criterion 引用证据）、`核验通过/未通过`。计数写成"核验 0/11 · 自评 6/11"。验收：fixture `project-flow.html?goalstop=1` 加一个未核验场景截图。
- **环境探针**：目标开始时 Host 采集 GPU（nvidia-smi）、CUDA 版 torch 是否可用、CPU/内存/磁盘，写进第一个目标回合 context 与章程。验收：本机跑出 "RTX 3070 Ti, CUDA torch: no"。

### P1　选题关（研究类目标；最大杠杆）
借 co-scientist 锦标赛 + Jr. AI Scientist 的"先写基线局限" + 执行差距研究的"先试点"：
1. 章程 kind=research 时，阶段 0 的出口条件是 `weave_directions`：≥3 个候选，每个含 最接近的已有工作（可打开的链接）、新在哪、一条**试点命令**（≤10 分钟、固定预算）、期望信号、放弃条件。工作区已有成果时，其中一个必须是"继续现有课题"，并写出现有工作的 3 条局限。
2. Host 把试点作为一轮短子线并行跑（复用 goal_lines，cap 小），结果回填。
3. Host 用**核验模型**做两两比较（3 个候选 = 3 次比较，按"重要性 × 新意 × 可行性（试点结果）"），得出排序与理由；lead 选定并写 note，可推翻排序但须给理由。
4. 未过选题关前，实验类写操作被 charter 门禁挡住（同现有 HELD 机制）。
成本估计：试点 3×(0.2–0.5)M + 比较 3×0.1M ≈ 1–2M 加权；本次总量 8M+，可接受。
验收：用本次 AI-RAN 提示重跑，记录里出现 3 个候选、3 份试点结果、排序与选择理由；人工判断选题理由是否成立。

### P2　早审（中途审稿，而不是只在末尾）
借 Agentic Reviewer（自带检索）+ Agents4Science（防谄媚）：
- 触发：第一批 ledger 主张出现后，或用掉 30% 时间/预算（取先到者），Host 起一次"预审"子代理（核验模型，无会话历史，带 _trace）。
- 任务：先自行检索最相近工作（多粒度查询），**先写拒稿理由**，再给"投主会还缺什么"；输出直接走 `goal_charter.add_review_round` 变成标准，不判 pass/fail。
- 本次若有它：命题 1 的平凡性与错误、新意被 [R5]/GMI 缩放收窄、缺替代方法对比、单种子，都会在约 10:30 被提出，而不是不确定何时。
验收：回放本次 10:45 时点的工作区，预审输出至少命中上述 4 点中的 3 点。

### P3　加证据算子（用闲置算力）
借 AI Scientist v2 的 replication 节点 + AlphaEvolve 的评估器思想：
- weave_claims 的每条主张已带复现命令。新增可选字段 `scale`：如 `{"seed": "--seed", "samples": "--slots"}`。
- Host 在 lead 写作/整合阶段（或机器空闲时）自动开一条"加固线"：对 headline 主张用 3 个种子、更大样本重跑，汇总均值/置信区间，回写主张的 `replicated` 字段；不一致的主张标红交回 lead。
- 本次可做：4 信道 × 3 种子 × 4096 时隙（GPU 上每条约 1–2 分钟量级）。
验收：本次工作区上，加固线产出 `results/replication_*.json`，6 条主张中至少 4 条带 CI；标准 C8 有客观依据。

### P4　模型分工
- 默认：执行用便宜模型；**选题比较、预审、终审**用最强可用模型（已有 `set_checker_model`，把研究类目标的默认值改为"最强可用"，带单次上限）。
- 依据：本次的命题 1 错误、选题惰性主要是推理质量问题；审稿环节 token 占比小（本次子线+主线 8M，审稿约 0.5–1M），换强模型性价比最高。
验收：同一目标用 flash 审 vs 强模型审，对比 major 条目的命中率（人工标注）。

### P5　发现记忆（低优先）
借 Kosmos 世界模型 / DeepScientist Findings Memory：把 notes 中的假设与结论结构化为 `findings`（假设、状态 supported/refuted/untested、证据 claim id、来源 line），lead/子线/审稿共享。现有 notes + ledger 已覆盖大部分，等 P1–P3 落地后看是否还缺。

### P6　评测集（判断"是否顶尖"的唯一办法）
- 5 个长任务：本次 AI-RAN、一个 ML 小课题（有公开基线）、一个数据分析、一个软件、一个文献综述型；每个固定模型、预算、时长。
- 评分：盲审 rubric（Agentic Reviewer 式 1–10 + 标准通过率 + 主张复现率），每任务人工抽查 2 条主张。
- 对照：同模型下 Codex /goal、Claude Code；Loom 版本间回归。
- 不做：完整 MCTS/进化搜索（AIRA：先做强算子）；自动投稿。

## 4. 顺序与预期

| 阶段 | 内容 | 估时 | 预期效果 |
|---|---|---|---|
| 1 | P0 全部 | 1 天 | 消除本次 36 分钟闲置、误导性提问、泄漏文字、0/11 误读 |
| 2 | P1 选题关 + P4 默认强审 | 2–3 天 | 方向由比较产生；投入约 +1–2M/目标 |
| 3 | P2 早审 + P3 加固线 | 3 天 | 方向性问题提前 1–2 小时暴露；headline 数字带 CI |
| 4 | P6 评测集首轮 | 2 天 + 运行 | 第一次有客观分数 |
| 5 | P5 视评测结果决定 | — | — |

每一阶段按现有流程验收：脚本化模型单测 + 真实模型实跑（E:\ai run 或 C:\tmp 隔离副本）+ 真实 Electron hub/phone 显示检查。

## 参考
- AI Scientist v2: https://arxiv.org/abs/2504.08066
- Kosmos: https://arxiv.org/abs/2511.02824 ；https://edisonscientific.com/articles/announcing-kosmos
- AI co-scientist: https://deepmind.google/blog/co-scientist-a-multi-agent-ai-partner-to-accelerate-research/
- DeepScientist: https://arxiv.org/abs/2509.26603
- AIRA-dojo: https://arxiv.org/abs/2507.02554
- Jr. AI Scientist: https://arxiv.org/abs/2511.04583
- Ideation–Execution Gap: https://arxiv.org/abs/2506.20803
- Agents4Science 2025: https://arxiv.org/abs/2511.15534
- Stanford Agentic Reviewer: https://paperreview.ai/tech-overview
- Hidden Pitfalls of AI Scientist Systems: https://arxiv.org/abs/2509.08713
- AlphaEvolve: https://arxiv.org/abs/2506.13131
- Anthropic long-running harness: https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- Codex goals: https://developers.openai.com/codex/use-cases/follow-goals
