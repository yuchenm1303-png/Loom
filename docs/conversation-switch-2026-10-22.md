# 切换对话：点下去到看见内容（2026-10-22）

起因：用户说"现在切换不同对话时手感很差，感觉也很卡，检查优化一下吧"。
切换对话是这个应用里最频繁的一件事，也是三层东西串在一起的一件事：Host 读出对话、一路传到渲染进程、渲染进程把它画出来。这一轮把三层各自量了一遍，改了最大的几处。

## 一、怎么量的

用的是用户自己的对话，但**只在副本上量、只记数字**（副本用完即删）：

- **Host**：把本机最长的 8 个对话（日志 6–26 MB，快照 0.1–6 MB）复制进一个临时 home，用真实的 app-server 进程和进程内两种方式读 `thread/read`（`presentationOnly`、`turnLimit: 20`，渲染进程切换时发的就是这个）。进程内的做法把 `_load`、`store.load`、日志解析、`_turn_records`、`json.dumps` 分段计时（不用 profiler，cProfile 把 4 ms 的东西报成 31 ms）。
- **渲染进程**：`desktop-react/scripts/perf/thread-switch-bench.mjs`。真实 App + `scripts/fixtures/perf-session.tsx` 的内存 Host，生产构建，真实鼠标点击（Playwright → CDP 输入事件）。fixture 新增三个参数：`heavy=N`（前 N 个对话各有自己的长历史）、`readMs=N`（`thread/read` 的延迟）、`real=a.json,b.json,…`（逐个对话回放真实的 `thread/read` 结果，形状和大小都是真的；回放文件放在仓库之外）。每次点击记：点击到**第一帧回应**（选中、标题、变暗的表面）、到**新内容出现**、之后主线程最长的一个任务、最坏的一帧间隔、脚本/布局/样式的 CPU。
- 对比用**交错 A/B**（基线 = `git archive HEAD` 的另一份构建，两边交替跑 6 轮取中位数）。这台机器的速度会因为别的会话在 2–3 倍之间摆动，单次的数字没有意义。

## 二、点一下到底花在哪（改之前）

一次"冷"切换大约 0.5–0.8 s，由三块组成：

**1. Host 读一个对话 170–370 ms（本机 8 个长对话的中位数 168–366 ms）。**

- **`thread/read` 每次把 session.json 完整解析三遍**：推理层的 `app_server_reasoning.thread_read`（`_load`）、基础的 `app_server.thread_read`（`_load`）、标题层的 `thread_title_override.thread_read`（`store.load`）各解析一次，一个 6 MB 的快照每次 20–110 ms。
- **日志每次整本重新解析**：`presentation_events` 缓存的预算是 32 MB，8 个对话轮流读，一个都留不住（只有读同一个对话时才命中）；而给缓存"称重"的 `_weight` 要把解析出来的整棵树走一遍，占了解析时间的一半以上。窗口只要最新 20 个回合，却把一个 25 MB 的日志从头解析到尾。

**2. 渲染进程 230–300 ms，全在一个连续的长任务里（真实的 20 回合窗口）。**

- 点击任务 117–148 ms：React 渲染 20 个回合（react-markdown 的 micromark 解析占 89 ms，约一半）；
- 紧接着下一帧的渲染任务 108–131 ms：样式 + 布局（`Layout` 76–98 ms）。
- 这期间一帧都没有：最坏的帧间隔 190–390 ms。而**第一个视觉回应就是这次换内容本身**，所以点击到屏幕有反应，要等整整这么久。

**3. 缓存几乎不命中。** 渲染进程缓存已读对话的预算是 16 MB（按保守估计，一个长对话 5–7 MB，11.7 MB 的那个超过单项上限、根本不进缓存），实际只装得下 2–3 个。来回翻五六个对话就是每次都去 Host 读。

## 三、改了什么

### Host

- **窗口读取不再解析整本日志**（`app/agent_runtime/event_index.py`，`FileAgentSessionStore.presentation_window`）。日志是追加写的，每一行的头部（`"turn_id":…,"kind":…`）在行首几百字节里：只给每一行建一个头部索引（行的位置、回合、种类；只建一次，之后只补追加的部分，靠"同一个文件、变大了、两端的字节没变"判断，和 `EventParseCache` 的增量读同一套验证），再按索引从文件里取出最新 N 个回合的行，只解析这些行。`thread/read` 的其余两样东西——当前回合的 `turn_completed`（`finalText`）和待批准请求——也按索引只取那几行。**输出和整本视图逐字节相同**：在 8 个真实对话的副本上，88 项静态对比（各种窗口大小、`beforeTurnId`、不存在的游标）+ 逐步追加每一类事件 + 截断 / 还原 + 末行没有换行，共 319 项对比无差异；`tests/test_event_index.py` 的 14 个测试覆盖这些形状，对索引、存储、服务三处的 21 个变异检查中 20 个被测试杀死，剩下一个（`size < offset` 的重复判断）与两端字节的哨兵检查等价。末行没有换行的日志退回整本视图。
- **一次读取只解析一次 session.json**（`FileAgentSessionStore.read_scope`）。生产服务的 `thread_read`（`BrowserPolicyLoomAppServerService`）在一个作用域里调用整条链，作用域内对同一个会话的再次 `load()` 拿到同一个对象，除非期间被保存过（按 mtime/大小/inode 判断；`_ensure_thread_model_metadata` 这类会存盘的路径照常存）。作用域属于调用它的线程。

### 渲染进程

- **只先挂载最新的两个回合**（`src/components/progressiveTurns.ts`，`Transcript.tsx`）。窗口的 20 个回合每多画一个约 40 ms（实测：2 个回合 ≈ 60 ms，4 个 ≈ 160 ms，20 个 ≈ 250 ms），而屏幕上看得见的通常只有最后一两个。第一次提交只带最新的 2 个回合（不够填满一屏就当场补到够，在绘制之前）；页面安静 1.2 s 之后，其余的回合分批（每批 3 个）在空闲时补上；读者往上滚时立刻补（只算读者自己离开了最新边缘之后的滚动：页面自己为了贴底做的滚动不算）。**有回合在跑的时候不补**：流式输出的当中塞进一个 100 ms 以上的任务就是一次卡顿，所以补回合要等这一轮结束、页面再安静 1.2 s；读者往上滚照样立刻补。补上去的内容在视图上方，视图跟随最新边缘，补上之前记下的高度被还回去（`scrollTop += 新增高度`），所以屏幕上看不到任何跳动。快速在对话之间翻的人，离开前这些旧回合从来没被建过。≤ 4 个回合的对话不拆。补齐之前 `<main class="transcript">` 带 `data-older-pending`，等整页就位的测试等它消失。
- **点击先被回应，再换内容**（`src/state/afterNextPaint.ts`，`openThread`）。从缓存里取已读过的对话，之前是在点击里同步 `applyThreadRead`，于是选中、标题、变暗的表面要等整个对话建完才第一次出现。现在先提交 `openingThreadId`（几十毫秒的一帧），等这一帧画出来再换内容；隐藏的窗口不画帧，最多等 120 ms。
- **缓存预算**从 16 MB / 8 项改成 64 MB / 12 项：长对话来回翻的时候命中。条件没变：只缓存空闲的对话、5 分钟、`updatedAt` 对得上。

## 四、结果

Host（进程内，8 个真实对话的 `thread/read` 处理时间，中位数 ms，改前 → 改后）：

| 对话（日志 / 快照） | 改前 | 改后 |
| --- | --- | --- |
| 8a6676c8（25.6 MB / 6.2 MB） | 366 | 118 |
| 439c7426（16.7 MB / 4.7 MB） | 283 | 101 |
| 8624f585（18.7 MB / 3.8 MB） | 264 | 109 |
| 61f47b87（13 MB / 2.2 MB） | 227 | 105 |
| 2d6e4fca（9 MB / 1 MB） | 223 | 88 |
| 78a56fac（6.3 MB / 0.8 MB） | 168 | 58 |
| fd8b584f（6.1 MB / 1.6 MB） | 176 | 34 |
| a0d92c4f（17 MB 日志，窗口 11.7 MB） | 239 | 195 |

最后一行是窗口本身就有 11.7 MB 的对话：它的时间在解析和序列化这 11.7 MB 上，索引帮不上。要再降只能让窗口变小（首屏先要 3–4 个回合，其余的后补），见"没做的"。

渲染进程，**在真实的 Electron 窗口里**（仓库里的 Electron、生产构建的 fixture、这台机器的 1707×1067 @165 Hz、150% 缩放，真实的鼠标事件；真实 `thread/read` 内容，4 轮交错 A/B，每轮 12 次点击，中位数；`thread/read` 在 fixture 里是瞬时的，Host 的时间要另外加）：

| | 第一次打开 改前 | 第一次打开 改后 | 再次打开 改前 | 再次打开 改后 |
| --- | --- | --- | --- | --- |
| 点击到**第一帧回应**（选中、标题、变暗） | ~235 ms | **~48 ms** | ~150 ms | **~40 ms** |
| 点击到新内容出现 | ~235 ms | ~175 ms | ~150 ms | ~130 ms |
| 点击后主线程最长的一个任务 | ~153 ms | **~95 ms** | ~95 ms | ~70 ms |
| 最坏的一帧间隔 | ~220 ms | ~155 ms | ~150 ms | ~95 ms |

同样的对比在无头 Chrome 里（生产构建，6 轮 × 14 次，混合）：第一帧回应 270 → 73 ms，主线程最长任务 172 → 92 ms，最坏帧间隔 267 → 145 ms，新内容出现 270 → 222 ms。

"新内容出现"只快了 15–25%，是因为最新两个回合的布局（真实窗口里 `Layout` 约 95–105 ms，一次布局 1120 个对象）才是大头，它不随挂载的回合数变化：同一个对话**第二次**布局只要约 40–50 ms，所以第一次里大约 70 ms 是字体 / 文本整形的首次成本（Blink 的整形缓存要按文本填一遍）。变的是**它不再站在第一个回应前面**，也不再是一整块冻住的画面：旧版点一下，屏幕先冻 ~235 ms 再一次性换掉；新版 ~48 ms 内选中、标题、变暗就到位，随后换内容。

## 五、没做的

- **首屏先要 3–4 个回合、其余后补**（两段式读取）。Host 和 Electron 之间每一跳（V8 序列化、套接字、反序列化）都要把整个 2.5–12 MB 的窗口走一遍，最大的那个对话光传输就有 100–250 ms。做法是 `thread/read` 先要小窗口、再用现有的"更早的回合"补；难处是 Inspector / 审查 / 产物这些按 `items` 汇总的面板，在补齐之前数字会变。这一轮没做。
- **`store.load` 剩下的一次**（6 MB 的快照约 60 ms）。可以改用已经缓存着的会话概览（侧栏用的那份）加上 `pending_approval` / `final_text` / `error` 三个字段，但三层包装各自会读会话的哪些字段需要一一核对，收益只有最大的几个对话。
- **回合里的 react-markdown 解析结果缓存**（按文本为键）。重访同一个对话的 markdown 解析可以省，但首次访问不受益，这一轮先用"少建"替代。
- **保持挂载**（最近几个对话的 DOM 留着只隐藏）。重访会是即时的，但转录区域有很多"只有一个滚动容器"的假设（`.conversation-stage > .transcript-scroll`），改动面太大。

## 六、陷阱

- **Windows 上的 CRLF**：工作区里很多文件是 CRLF，索引是 LF。改文件用 Edit 工具，或者读成行、保留每一行自己的行尾再写回；Git Bash 的 heredoc 会把双反斜杠吃成单个（脚本里写的 `\\n` 到了 node 里变成真正的换行），写带反斜杠的脚本要用 Write 工具。
- **测"补了一片之后、下一片之前"不能靠计时**：一片补完，下一片在几毫秒内就被要求，用 `locator.count()` 轮询去追会读到过期的数（第一版就因此偶发失败）。把 `requestIdleCallback` 换成测试自己的队列，手动放行。
- **Git Bash 把 `real=/real/0.json` 当路径转成 Windows 路径**：给 node 传带 `/` 开头的参数前设 `MSYS_NO_PATHCONV=1`。
- **`content-visibility: auto` 的块里 `innerText` 是空的**：测试在 `scrollIntoView` 一个旧回合之前要先发一个向上的 wheel（读者本来就是这样到旧回合的，同时也把视图从最新边缘上放开），否则视图还在跟随，布局一变又被拉回底部，目标又被跳过。
- **补回合会让滚动高度变大**：测试里不能再断言"历史加载后高度不变"，该断言"不缩小"和"最新回合的位置不动"。
- **`disabled-by-default-devtools.timeline.invalidationTracking` 会把每次样式重算放大三倍**：只看它给出的计数和原因，不看时间。
- **同样的 Host 读取，通过 Python 客户端量出来是 200–1000 ms，进程内只有 100–370 ms**：客户端的文本模式管道读 3–12 MB 一行很慢，量 Host 要进程内量。
- 已知本来就红的（不是这一轮引入的）：`css-has-invalidation`（terminal-run-control.css 的 `:has()`）、`browser-activity-ui.test`、13 个浏览器测试（component-surfaces、disclosure-motion、earlier-process、execution-sequence、model-usage、progress-feedback、project-send、readability-floor、right-tool-dock、runtime-effects、terminal-panel、weave-flow、workspace-visual，在 HEAD 上同样的断言处失败）。

## 七、测试和工具

- `tests/test_event_index.py`（索引窗口与整本视图逐项相同、追加 / 截断 / 替换 / 末行不完整 / 头部形状不对 / 迟到事件 / 待批准、没有日志文件时连"指向不存在的回合的游标"也和整本视图一样报错、分块索引、半途失败的索引不再使用、读作用域），`tests/test_thread_open_reads.py`（改成断言用的是窗口读取、并与整本视图和整本日志相同）。
- `desktop-react/scripts/tests/progressive-turns.test.mjs`、`after-next-paint.test.mjs`（单元）、`thread-switch-progressive-browser.mjs`（真实浏览器：第一帧只有最新的 2 个回合、之后只增不减、最新回合相对滚动容器底部的位置全程不动、读者提前上滚时立刻补、页面自己的滚动不触发补回合、读过的对话先画点击再换内容、短对话一次到位；跑着的对话的场景把 `requestIdleCallback` 换成测试手里的队列，一片一片放行，所以"补了一片之后、下一片之前"这个瞬间不取决于机器的速度：回合开始时已经要求的那一片被撤回、跑着的时候没有任何一片被要求、跑完后头 900 ms 也没有，之后才补齐并贴着最新边缘）、`conversation-home-navigation.test.mjs`（点击先被回应）。给每条行为的变异检查：关掉暂停、去掉跑完后的安静等待、`Transcript` 不告诉 hook 有回合在跑、关掉"页面自己的滚动不算"，都被这些测试杀掉。
- 改了断言的老测试：`conversation-entry-browser`（高度只增不缩）、`turn-changes-browser`（等整页就位、像读者一样先向上滚）、`transcript-follow-browser`（等整页就位再开始）、`tests/test_conversation_navigation_performance.py` 和 `tests/test_thread_switch_transition_contract.py`（缓存预算）。
- `scripts/perf/thread-switch-bench.mjs`：`cold | warm | ping | hover | custom --order 1,2,3`，`--read-ms`、`--hover`、`--query`、`--json`；生产构建的做法见文件头。
- `scripts/perf/thread-switch-electron.cjs`：同一个测试放进真实的 Electron 窗口（GPU、真实刷新率、真实输入事件），按"第一次打开 / 再次打开"分组给中位数，**引用数字用这个**；无头 Chrome 用软件绘制，适合看形状，不适合看毫秒。`BENCH_ORIGIN=http://127.0.0.1:5199 node_modules/electron/dist/electron.exe scripts/perf/thread-switch-electron.cjs`（origin 必须放环境变量里：Electron 拿到 http:// 参数会在跑脚本之前退出）。
