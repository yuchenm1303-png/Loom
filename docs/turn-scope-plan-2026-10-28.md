# 小问题被做成大排查：根因与修复方案（2026-10-28）

## 案例

会话 `223b796b`（「纽约ip」），10 月 28 日用户问「哪里可以买到好的越南出口ip」。

- 一轮 10 分 43 秒、31 步、38 次工具调用；模型时间 426 秒，每步重发 10.5 万到 15.2 万 token。
- 和问题有关的只有 4 次 web_search。其余是 20 次 exec（SSH/scp/curl 去探用户自己的
  腾讯云、洛杉矶 VPS、旧越南上游）、13 个写进 `artifacts/` 的探测脚本、
  `record_check` ×2（一次因引用无效失败）、`resolve_verification` ×1。
- 模型自己编了动机：「现有越南链路已经不通了……所以你才要重新买」。

运行的代码：安装版 v0.1.137，Host `sourceSha 982a48db`，`app/agent_runtime` 与 HEAD 无差异。

## 根因（按影响排序）

### 1. 旧会话的系统提示词永远不会升级（最主要）

- 会话保存的是提示词**全文**，`runtime.py:362 _upgrade_default_system_prompt` 只升级与
  `system_prompts.py:193 _LEGACY_DEFAULT_AGENT_SYSTEM_PROMPTS` 逐字相等的文本，其余一律当作“自定义提示词”保留。
- 早期默认提示词有好几版从没进过这张表。本机 174 个会话里：

  | 存储的提示词 | 会话数 | 最近使用 |
  | --- | --- | --- |
  | 340 字符（只有第一段） | 17 | 10-14 |
  | 1,111 字符（前三段） | 48 | 10-22 |
  | 1,572 字符（前三段 + 图片段，本案） | 8 | 10-28 |
  | 6,472 字符 | 3 | 09-26 |
  | 当前 v13 | 16 | 10-28 |

  共 76 个会话卡在旧版本上，而且越是长期在用的老线程越容易卡住。
- 本案的 1,572 字符版本缺少这些段落：「做最小的直接动作 / 不做顺手的大排查 / 保持用户要求的范围 /
  趋于收敛 / 不把项目当草稿区 / 并行调用 / 外部系统有限等待 / web_search 规则」。
  请求时 `system_prompt_with_feedback` 只追加了 `[LOOM_COMMUNICATION]` 尾段，正文仍是旧的。
- 用户没有设置自定义提示词的入口。`create_session` 的调用方只有：运行时默认值、
  fork 复制（`app_server.py:1208`）、子代理（同样的开头 + 额外段落）。
  所以「逐字比对 + 快照表」这种做法本身就是错的：每改一次提示词，就有可能把一批会话孤立出来。

### 2. 被取消的上一轮在历史里看起来像「还在进行」

- 本线程前两轮（10-13 跑了 1,210 秒、10-22 跑了 2,075 秒）都是用户中途停掉的。
  新问题之前，历史的最后几条是 `assistant: 链路结构已经清楚了。我再确认 iPad 客户端……` + 两个工具结果。
- 运行时不在模型历史里写任何“上一轮已被用户停止”的标记
  （`TURN_INTERRUPTED` 只是事件，不进消息）。模型因此把新问题当成用户在排查中途插的一句话，
  接着排查下去。

### 3. 提示词里没有「按请求大小办事」的规则（v13 也没有）

- 「Default to action」只讲了不要拖延，没讲范围：问题就答问题；只有用户要求时才去探查或改动用户的系统；
  旧线程里没做完的事是背景，不是待办。
- 第 107 行「关于本机/环境的问题就 exec」和第 113 行「声称连不上服务器之前先用工具验证」，
  在代理/服务器类线程里会被泛化成「先去探一遍」。

### 4. 验证闸门把临时脚本当成源码改动，逼模型去「验收」

- `development_verification.py:455` 的 subjects 来自 diff tracker 记录下的、本轮工具写过的所有路径，
  过滤只有 `source_kind`：固定的目录名表 `_IGNORED` + 扩展名。`artifacts/vn_auth*.py` 被 `.gitignore:67`
  忽略了，仍被算作 functional 源码。
- 一旦有 subject，`context()`（`:542`）**每一步**都注入「A completion attempt cannot finish with unresolved
  changes … Execute the missing verification now.」。模型随后花了几步做 `record_check`/`resolve_verification`，
  还说「我记录一下验证结果并提交这些诊断脚本」。

### 5. 草稿目录对模型几乎够不着

- 提示词要求临时文件放进“harness 提供的 run scratch 目录”，但路径只能通过 `get_run_scratch_dir`
  工具拿到（`evidence_tools.py:155`），`LOOM_RUNTIME_STATE` 里没有；而且那个目录在工作区外，
  `write_workspace_text`（`tools.py:110 resolve_workspace_path`）写不进去。
- 提示词自己的例子写的是 `![preview](artifacts/result.png)`，模型就学会了把东西放 `artifacts/`。
- 结果：跳不出工作区 → 写进项目 → 触发第 4 条。

### 6. 每一步都很贵，放大了以上所有问题

- MiniMax-M3.1-flash 的窗口未声明（`context_limits.source = runtime_fallback`、`window_known = false`），
  按 272k 回退，245k 才压缩，所以一个问题每步都背着 10 万以上的旧排查。
- 31 步里约 15 步只输出 87–90 个 token，也就是一步只调一个工具。旧提示词里没有并行规则。
- Windows 执行摩擦又多耗了 4–5 步：`cmd /c` 拆坏多行脚本、scp 不认中文路径、SSH 超时。
- 次要：token 估算偏高约 45%（第一步估 15.3 万，实际 10.5 万）。

## 修复方案

### P0-1 提示词改为“引用”，不再存全文（根治第 1 条）

- `AgentSession` 增加 `system_prompt_source: "default" | "custom"`。`default` 会话在构造请求时
  （`runtime.py:1507`）现取 `DEFAULT_AGENT_SYSTEM_PROMPT`，存储里不再写全文
  （为兼容旧读取方，可以写，但不作为权威）。
- 一次性迁移规则：存储文本的**每一段**都是 Loom 曾经发布过的段落（取所有快照和上表四种旧文本的段落并集），
  就判为 `default`。子代理提示词多出自己的段落，所以保持 `custom`。fork 继承来源会话的 `source`。
- 迁移完成后删掉 `_LEGACY_DEFAULT_AGENT_SYSTEM_PROMPTS` 这套逐字快照机制。
- 验收：拿本机 174 个 `session.json` 的副本跑迁移，76 个旧默认全部变成 `default`，
  14 个子代理一个都不动；单测覆盖 340/1111/1572/6472 四种文本、子代理、fork。

### P0-2 被停止的轮次在历史里留下明确标记（第 2 条）

- 一轮以 cancelled/interrupted/failed 结束时，在下一轮的用户消息之前插入一条运行时消息（不是用户指令）：
  「上一轮在完成前被用户停止（或失败）。未完成的工作只是背景；除非用户要求，否则不要续做。」
- 位置：开始新一轮、组装历史的地方（`turn_runner.py` / `instructions.py`），持久化到消息里，
  压缩时也要保留。
- 验收：取消一轮后发新消息，第一个 `model_requested` 的消息里有这条标记。

### P0-3 提示词 v14：范围规则（第 3 条）

在 `_DEFAULT_AGENT_TASK_PROMPT` 的「Default to action」段后加一段，大意：

> 让工作量和请求相称。问题、推荐、比较类请求，直接用回答（必要时加搜索）作答；
> 只有用户要求时，才去探查、测试或改动用户的服务器、账号或配置——可以在答案末尾提议。
> 在已有线程里，用户最新的消息就是当前任务；之前没做完的工作是背景，用户没要求就不续做。

同时把第 107 行限定为「用户问到本机状态时」，把第 113 行限定为「用户在问部署/服务器状态时」。
v13 原文放进旧快照（如果 P0-1 已删掉快照表，就并进段落并集）。

验收：在本案会话的副本上用 minimax-m3.1-flash 重放同一句话，3 步以内答完，
0 次 exec/SSH，回答里给出购买渠道，最多提议去查旧线路。

### P1-4 验证闸门只管项目源码（第 4 条）

- subject 入口过滤掉：git 忽略的路径（每次扫描 `git check-ignore --stdin -z` 批量查一次，
  非 git 工作区沿用 `_IGNORED`）、run scratch 目录、工作区外的路径。
- 轮中途只注入一行简短状态（N 个文件待验收）；「Execute the missing verification now / cannot finish」
  这类要求只在 `before_finish` 收尾时出现。
- 验收：在被忽略的目录写 `.py` 不产生 subject；改 `app/x.py` 仍会产生；收尾闸门行为不变
  （沿用 `test_development_verification*` 套件）。

### P1-5 让草稿目录可用（第 5 条）

- 在 `LOOM_RUNTIME_STATE` 里直接给出 `scratch_dir` 路径。
- `write_workspace_text` / `read_workspace_text` 接受 `scratch:` 前缀（或单独的 `scope: "scratch"`），
  解析到本轮的 run-artifacts 目录，不需要先调工具。
- 把提示词里的图片例子从 `artifacts/` 改成中性路径。
- 验收：模型写探测脚本时落在 scratch，项目 `git status` 不变。

### P2-6 降低每步成本（第 6 条）

- 在模型档案里声明 minimax-m3.1-flash 的真实上下文窗口，不再走 272k 回退。
- 新一轮开始、且距上一轮超过几小时、历史超过约 60k 时，先用已有的 idle compaction 把旧轮压成摘要
  （需要先查清这次为什么没发生：线程只压缩过一次）。
- `LOOM_RUNTIME_STATE` 的 Windows shell 提示：`cmd /c` 不支持多行，复杂脚本写成文件后用
  `powershell -File` 或 `python` 执行；路径含非 ASCII 字符时，scp/ssh 用 scratch 下的纯 ASCII 临时路径。
- 校准 token 估算（偏高约 45%）。

## 顺序与回归

1. P0-1 → P0-3 → P0-2：先让所有会话吃到新提示词，再加规则，最后补取消标记。三处都只改运行时文本和迁移。
2. P1-4、P1-5 一起做（它们是同一条因果链）。
3. P2 单独排期。

每一项都用「复制本案会话 → 同一句话重放」做端到端验收，并记录步数、exec 次数、用时和输入 token。
私有内容只放 scratch，不进仓库。
