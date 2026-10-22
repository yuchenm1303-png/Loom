# Loom 真实任务评测 v1

从仓库根目录运行 `python -m evals.run`。默认只检查独立判分器对空/错误产物的拒绝，不调用模型。

- `python -m pytest tests/test_agent_task_evals.py`：正反判分用例及真实 runtime 的脚本模型错误完成声明回归。
- `python -m evals.run --mode real --model MODEL --allow-paid --output eval-results/MODEL`：付费真实评测。凭据使用既有 LOOM_API_KEY / OPENAI_API_KEY 等环境配置；缺失时逐项 not_run。
- 用 `--task code-sum` 可重复指定子集；用不同 output 目录保留重复试验。
- `--prompt-prefix` 附加系统提示；`--context-limits '{"context_window_tokens":32768}'` 传给既有 CLI 的上下文解析器。`--context-label` 仅标记实验，不改变行为。

## 设计与复用

已检查 tests/scripted_agent_platform.py、test_task_convergence.py、test_evidence_contract.py、test_ordinary_conversations.py、loom_cli.py、runtime.py、contracts.py 和现有 CI。复用生产 CLI 的模型配置与工具注册、DurableAgentRuntime.start_turn、FileAgentSessionStore 及执行事件；没有自建推理/工具循环。沿用成熟的“固定 fixture → 执行 agent → 外部 oracle”模式，以及现有 evidence 案例拒绝无凭据完成宣称的原则。

任务定义在 tasks.py，每项包含独立输入环境、指令和 expected；编码任务通过独立子进程调用函数，文件任务比较解析后的 JSON，浏览器任务还要求成功 browser 工具事件，恢复任务检查 checkpoint 后关闭并重建 runtime，使用同一持久化 session 继续。expected 不发给 agent。12 项分别覆盖求和、稳定去重、slug、CSV 聚合、JSON 清理、中文文本、浏览器价格、表格、跨页链接、求和恢复、排序恢复、计数恢复。

环境：每项新建系统临时目录，workspace 与 state 分离；浏览器只提供 loopback 合成页面。所有测试无需业务账户或业务数据。workspace 权限沿用 Loom 的执行安全边界，临时目录本身不是 OS 沙箱：不可信模型应在无个人数据的 disposable VM/container 中运行，浏览器需独立测试 profile。绝不把生产浏览器登录状态作为 fixture。

## 指标与解释

JSON 包含 schema、suite 哈希、Git revision/dirty、模型、提示哈希、上下文配置、逐项状态、耗时、可获得 tokens、审批请求数、事件分类数及失败原因。null 表示未知；零 tokens 不误当免费。interventions 当前统计审批请求而非已批准次数，其他人工介入不可观测。false_completion 表示 runtime completed 但独立判据失败，是操作性代理指标，不是对任意自然语言声明的语义判定。success_rate 仅以已执行真实任务为分母，未执行不算成功，oracle 模式没有成功率。

恢复任务目前验证跨 runtime 重建/持久化的两阶段任务，不代表已验证进程崩溃、网络中断或小时级任务。浏览器能力缺失会失败，不能退化成读取 HTML 文件通过。编码判分是基础代表性用例，不代表完整隐藏测试覆盖。

默认 20 模型步骤/60 工具调用和请求超时 60 秒；CI 外部 job 超时兜底。真实运行建议在外部作业设置总时间与费用上限。报告不保存原始提示、工具输出或异常文本，避免泄露凭据；未知值不补估计。当前没有成本定价换算。对比时固定 suite、模型配置、运行时 commit、机器/浏览器版本；每组合至少重复 5 次，报告每次结果及通过数/样本数，不把一次通过当统计结论。依赖版本尚未自动锁定到报告，应由实验环境 lockfile 保留。

## 扩展到 40 项（新增 28 项）

编码：多文件 API 修复、回归测试补齐、依赖升级、SQL 参数化、时区边界、异步取消、CLI 参数解析。
文件：多编码 CSV、Excel 公式、PDF 表格、DOCX 修改、ZIP 路径安全、重复文件清理 dry-run、损坏 JSON 恢复。
浏览器：分页筛选、动态加载、表单校验、下载文件、上传合成文件、登录测试账户（凭据 opt-in）、可访问性导航。
恢复：工具超时重试、网络断线、进程 kill 后恢复、上下文压缩、审批挂起恢复、重复副作用防护、长日志断点续跑。

新增项必须给出输入 fixture、不可见 oracle、资源/凭据需求、超时与负向验证。登录等凭据任务须独立 opt-in，缺少秘密报告 not_run。建议后续加入容器硬隔离、事件附件脱敏归档、重复试验置信区间及模型/提示/context 的矩阵比较器。

## 基线

baseline/oracle 是脚本判分基础检查；baseline/real-not-run 明确记录未启用付费调用，没有真实模型成功率。pytest 的脚本 provider 只验证执行/判分链路，不是模型能力结果。真实模型、浏览器实际操作、长时间崩溃恢复均不能据此声称通过。

验证记录：本次本地执行 test_agent_task_evals.py、test_evidence_contract.py、test_task_convergence.py，共 42 项通过。独立 agent-evals-paid.yml 仅 workflow_dispatch 触发，使用专用 LOOM_EVAL_API_KEY secret；普通 PR CI 不读取模型凭据、不产生模型费用。
