# Codex 核心行为对齐：本轮实现与验收

本轮以 `docs/codex-gap-audit-2026-09-08.md` 的实证问题为起点，参考开源 Codex `d6489472f3c15e87d2d7763a5fde033545c530f8`。这是一次核心运行时改造，不是完整 Codex 产品或 Responses API 的一比一实现。

## 已落地的变化

| 领域 | 当前实现 | 主要代码 |
| --- | --- | --- |
| 执行循环 | Core/Context 共用唯一 TurnRunner；上下文和模型执行通过服务接入 | `turn_runner.py`, `model_execution.py`, `context_budget.py` |
| 完成判定 | 截断、过滤、未知结束原因进入失败；不执行不完整工具调用；正常兼容后端保留空 finish_reason 合同 | `turn_runner.py` |
| 流式终态 | OpenAI 与通用流累积器都要求明确完成标记；EOF 不再等于成功；读取结束关闭流 | `ai/openai_streaming.py`, `ai/streaming_platform.py` |
| 取消 | 请求作用域的取消上下文传入 provider reader；runtime 不等待不可取消模型调用；迟到结果不得写历史 | `ai/execution_control.py`, `model_execution.py` |
| 请求边界 | 默认最多 16 个仍在运行的模型请求、150 秒本地 deadline；放弃的请求仍占槽，直到真实调用退出 | `model_execution.py` |
| 重试 | 模型传输错误有限退避重试；每次新 step_id；工具只在完整响应提交后执行；旧流式卡片关闭 | `turn_runner.py`, `app_server_streaming.py` |
| 审批恢复 | 校验权限、sandbox、环境策略、工具 schema/来源/实现和可用模型元数据的摘要；不接受未知旧绑定 | `execution_binding.py`, `runtime.py`, `tool_search_runtime.py` |
| MCP 绑定 | 将明确的 server 配置加入绑定摘要；恢复同一批多个 deferred 工具 | `mcp_runtime.py`, `tool_search_runtime.py` |
| 项目指令 | 从 Git 根到 cwd 读取 AGENTS.md；支持 AGENTS.override.md；来源标识、32 KiB 限额、越界链接限制 | `instructions.py` |
| 上下文 | 把消息、schema、图片估算纳入预算；安全工具组边界自动压缩，同一回合继续；保留最近用户原文 | `context_budget.py` |
| 技能 | 已加载技能的内容快照随会话保存，在压缩及恢复后继续注入；总量限制 32,768 字符 | `skills_runtime.py`, `storage.py` |
| 插话 | `turn/steer` 校验当前 turn_id；磁盘 inbox 接收；安全边界消费；未执行的旧工具调用形成取消观察 | `runtime.py`, `storage.py`, `app_server.py` |
| 持久化 | event+snapshot redo journal；崩溃后补齐双方；截断尾部恢复；提交级进程锁与完整会话执行租约 | `journal.py`, `storage.py` |
| 未知副作用 | 中断后缺失工具结果明确写为 outcome_unknown，提醒先查证，避免宣称副作用没有发生 | `history.py` |
| 资源管理 | SQLite 连接显式关闭；runtime.close 取消活动模型回合 | `durable_state.py`, `agent_graph.py`, `memory_store.py`, `runtime.py` |
| 环境策略 | all/core/none 继承、include/exclude、大小写规则、secret 拦截；作为 step 快照传给 exec | `shell_environment.py`, `step.py`, `process_tools.py` |
| Schema | 标准 JSON Schema validator，本地 $ref/组合/数值边界；缓存校验器；禁用外部引用的隐式网络读取 | `tools.py`, `pyproject.toml` |
| 文本补丁 | 保留 JSON changes，同时支持 Begin/End Patch、增删改、Move to、顺序精确上下文 hunk | `patch_format.py`, `patch_tools.py` |
| 慢客户端 | 通知溢出后可靠送出 thread/resync；桌面端清理未提交流并重新读取权威快照 | `app_server.py`, `desktop/window.py` |
| 网页快照 | 在同一服务锁内读取状态与活动标记，避免任务结束时返回旧审批和空闲标记；增加受控并发回归 | `web_ui.py`, `test_web_ui.py` |
| Windows venv | 根据实际 Python launcher 发现 pyvenv.cfg 与基础解释器目录，不依赖已激活 VIRTUAL_ENV | `sandbox.py` |

## 兼容与使用

- 现有 CLI、桌面及 App Server 均使用统一循环。旧 runtime 子类仍作为兼容适配层保留，未声称已经消除所有深继承。
- `LOOM_CONTEXT_WINDOW_TOKENS` 默认 `32768`；`LOOM_OUTPUT_RESERVE_TOKENS` 默认 `4096`。使用较小窗口的模型时必须相应配置。估算为保守启发式，并非每家 provider 的精确 tokenizer。
- 手工 compaction 仍不能在活动回合调用；自动 compaction 由 TurnRunner 在采样边界控制。
- 旧会话可以读入，缺失的新字段使用默认值。旧 pending approval 没有绑定摘要时不能直接批准执行；应拒绝该旧请求，再在当前配置下生成新请求。
- 自定义工具工厂应设置稳定的 `binding_key` 来标识外部配置/实现版本。任意 Python 对象的所有动态状态不可能自动可靠序列化；不要把未声明的可变权限配置藏在 handler 对象中。
- 技能在显式 skill_load 时捕获版本；重新加载同名技能可以更新快照。历史与摘要不能重新定义 runtime 权限。
- `apply_patch` 输入接受 `{"patch": "*** Begin Patch\n...\n*** End Patch"}` 或原有 `{"changes": [...]}`，不能同时提供。文本匹配故意拒绝歧义；尚不支持 Codex 的所有宽松匹配或远端 environment 扩展。
- `turn/steer` 输入为 `threadId`、`turnId`、`input`。正在执行的工具不会被强行回滚；其后尚未执行的旧工具会取消，模型在下一步看到新方向。
- `thread/resync` 含 threadId、reason、dropped。其他客户端应在收到后重新读取 thread/read，而不能仅靠有损增量维护最终状态。

## 验证证据

新增 `tests/test_alignment_reliability.py` 覆盖截断终态、取消阻塞调用、绑定变更/重启、注入磁盘失败的恢复、损坏日志尾部、项目指令、自动压缩、工具组配对、schema、插话、重试副作用、环境策略、通知重同步、文本补丁、技能生命周期、venv 路径和跨进程执行租约。

使用项目 `.venv` 安装 `.[dev,desktop]` 后运行完整 pytest，并运行 compileall 和 CLI 帮助 smoke。最终完整回归收集 364 项，360 passed / 4 skipped（退出码 0）。没有通过更换断言把运行时失败掩盖为通过。

桌面文件在本轮期间有其他任务的并行修改，本轮只添加必要的 resync 接口衔接，保留其他修改；全套结果反映测试时整个工作区，而非这些桌面改动全由本轮完成。

## Windows MXC 真机结果：未通过

本轮额外在缓存目录安装固定的 `@microsoft/mxc-sdk@0.8.0`，运行真实 probe 和三个原本会跳过的 enforcement 用例，没有改变全局安装或运行提升权限的 host-prep。

1. Probe 选择 appcontainer-dacl，并报告系统盘 metadata / NUL device 的宿主准备提示。
2. 首轮三个用例均因 Python venv 的 `No pyvenv.cfg file` 失败；已修复 runtime 路径发现，并补回归用例。
3. 再运行时进程启动超时，随后 probe 也出现超时。三个真机用例没有通过，无法据此宣称文件/网络隔离已经在本机验收。

正常全套中的三个 MXC 用例仍因未显式设置环境变量而 skip；该 skip 不能抵消上述实际失败。应在准备好的 Windows 沙箱宿主上重复 enforcement 测试。REQUIRED 保持失败关闭，没有用关闭隔离或增加用户权限掩盖问题。

## 仍未宣称完成的工作

- 完整 Responses API / response item 协议、原生 Codex App Server 全量线协议兼容。
- 全部 runtime 继承适配层迁移为组合服务；本轮已解决两份主循环，并建立独立服务边界。
- 精确 provider tokenizer、大量图片及极端超大历史的分段摘要回退；无法安全压缩时明确失败。
- 完整细粒度文件/网络/命令规则、Windows 宿主 sandbox 部署与全部平台的强隔离验证。
- 外部工具副作用 exactly-once 保证。当前使用未知结果和对账语义，不伪造事务保证。
- 同模型、同仓库任务集的端到端成功率和成本比较；本轮没有请求真实付费模型进行评测。

这些边界独立于本轮已通过的核心回归，后续应按同一固定 Codex 源码基线继续验收。
