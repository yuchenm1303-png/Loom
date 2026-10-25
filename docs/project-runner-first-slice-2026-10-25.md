# 持久工作型 Agent：第一批后端实现

状态：已实现单 worker 后端执行切片，不是完整重型功能。实施目标见 [首版规格](long-running-project-spec-2026-10-25.md)。

## 已可运行

- Host 的生产 BrowserPolicy Service/Controller 加载 Project Runner，使用现有 agent runtime、凭证准备、权限和工具执行链。
- 单独 projects.sqlite3 保存项目、幂等命令、工作计划、会话派发意图、准备失败回执、结果和可分页事件。操作带 operationId 和 expectedRevision。
- 创建后不启动模型；明确 start 后，read-only planner 把目标拆成有验收要求的顺序任务，worker 每轮实现并返回 continue/candidate/blocked。
- 一个项目在独立的 detached Git worktree 中工作，保持源目录；工作会话按任务隔离，同一任务可跨多轮接续；原始会话记录仍由 runtime 保存。
- 派发前保存 session/turn ID；已落盘的终结 turn 可在重新创建调度器后导入，不重新调用模型。没有可核对的终结事实进入 needs_review，不自动重放工具。
- 暂停阻止后续派发，让当前轮到边界结束；取消持久化后协作取消运行。准备阶段也检查取消，runtime 激活时再次检查，关闭首次采样前的竞态窗口。
- 正常审批继续由现有界面处理，项目不得越过 waiting_approval。预算按完整 episode 结算并在下一 episode 前检查，同时提供 maxEpisodes 上限。
- 只有一个调度器可持有 projects scheduler 的 OS 文件锁；后台定时器不依赖 renderer，空闲不调用模型。
- 内部会话使用固定标签并关闭自动标题生成，避免标题模型在项目账本之外额外调用。

## RPC

通过现有认证的本地 App Server 连接调用，不新增网络监听端口。`initialize` 返回 projectRunner capability。

```json
{"method":"projectRun/create","params":{"operationId":"unique-create-id","threadId":"existing-thread-id","objective":"实现一个可保存数据的项目","tokenBudget":100000,"maxEpisodes":30}}
```

create 返回 run，初始 state=created、control=paused。它要求 source thread 的目录是已有提交且工作区干净的 Git 仓库根目录；首版不自动收纳未提交文件，也不自动 git init。

```json
{"method":"projectRun/start","params":{"operationId":"unique-start-id","runId":"returned-run-id","expectedRevision":1}}
```

`projectRun/get`、`projectRun/list` 查看状态；`projectRun/events` 用 afterSequence/limit 补齐记录；`projectRun/pause`、`resume`、`cancel` 使用相同控制参数，每次取最新 revision。重复同一 operationId 和输入返回当前投影，不重复执行；同 ID 换参数会拒绝。

## 首批明确边界

所有 worker 候选任务结束后，项目停在 **ready_for_review**，不宣称 completed。还没有独立验收 agent、验收证据 gate、自动集成/main 发布、多 worker、项目专用前端和多机执行。模型报告的 candidate 只是待审实现，不能证明功能通过。

预算是 **between_episodes 软限制**：一轮内的工具/模型循环可能超过 tokenBudget，回执未知的用量也不能精确计量；它不是采样级硬额度。maxEpisodes 也不是每轮模型步数上限。用户能通过现有 turn 中断取消运行；严格调用预算 hooks 属于下一批工作。

未知结果会保留工作目录、原 session/turn 和原因，首批不提供一个无条件 retry 来绕过核对。暂停只控制后续轮次；不虚报当前轮已经停止。worktree 不等于 OS sandbox，实际工具权限仍受 Loom runtime 能力约束。

本批自动化用脚本化模型驱动真实 runtime、真实 Git worktree 和真实 SQLite；验证工具写入、连续轮次、只读规划、审批、准备中取消、重启结果导入、未知结果不重放、预算、幂等与版本冲突、OS owner lock、无 renderer 后台运行。没有做真实付费模型项目或七天稳定性测试。

最终在干净 origin/main 基线叠加本任务七个文件验证：项目专项、轻量常驻、生产 Host/browser policy、App Server/协议、既有 durable goal/多 agent 恢复共 89 项通过（48.46 秒）。验证环境使用 bundled Python 与已有 .cache/test-python 依赖，不依赖主工作区其他任务的未提交运行代码。包含生产 Host 同时加载两种调度器、候选提交跨任务保留、取消尚未退出时不释放单 worker 槽位的回归。
