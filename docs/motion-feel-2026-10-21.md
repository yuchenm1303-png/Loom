# 动效的手感：一点就动、走得干脆、按下有回应（2026-10-21）

起因：用户说"帮我检查一下各处动效优化一下手感吧，顺便整理一下动效体系，可以随便重构动画，你有最高权限随意修改"。
10-18 的《动画语言》（`docs/motion-language-2026-10-18.md`）统一了时间和曲线；这一轮看的是**手感**：从手到屏幕的那一段——点下去多久屏幕有回应，关掉时是不是立刻开始走，动画还没走完时再点一下能不能把它拉回来，按下的那一刻有没有反馈。

## 一、怎么量的

不靠看，靠量；而且在用户真实用的条件下量：

- **真实 Electron 窗口**：仓库里的 Electron、生产构建的 live-app fixture、这台机器的 165Hz / 150% 屏幕，用 `webContents.sendInputEvent` 发真实的鼠标和键盘事件；每一帧记录主体元素（浮层、面板、被挤开的输入框……）的位置、透明度、缩放，以及输入事件的时间。工具：`desktop-react/scripts/motion-lab/feel-electron.mjs`（无头版 `feel.mjs`，对比两次 `feel-compare.mjs`）。
- 每个交互读四个数：从输入到**第一帧变化**（回应）、到一半、到九成、最大的单帧跳变；"来回"场景（动画还在走时第二次点击）看它是否平滑掉头。
- **主线程**：`toggle-cost.mjs`——每次开关面板，从点击到画出来主线程花了多少（脚本 / 样式 / 布局），样式重算碰了多少个元素、为什么。
- **按下**：`press-sheet.mjs`（每类控件静止 / 悬停 / 按下三张截图对照），外加一次 507 个控件的按下行为统计（用 DevTools 协议强制 `:active` 后读计算样式）。

## 二、找到的问题

### 1. 关东西总是慢半拍

所有退出都用 `--loom-ease-in`（`.42,0,.72,.2`）：时间过半时只走了 15%，85% 的时间才走到 63%。浮层按 Esc 后要 ~100ms 才淡掉一半；停靠面板更糟——一条 `body.loom-panel-motion … { transition-duration: var(--loom-dur-panel) !important }` 把所有面板的退出都拉成了 390ms 的 ease-in：**点关闭后 110–170ms 面板几乎原样不动，~370ms 才走一半**，而它下面的对话内容在点击后 30ms 就开始往回滑了——内容在一块迟迟不走的面板底下穿过。

### 2. 侧栏开关有一帧 80–100ms 的冻结

侧栏用 View Transition：浏览器要先把旧画面画出来、截图，才开始执行更新。生产构建里点击后要 95–170ms（中间一帧 ~100ms 的冻结）屏幕才开始动；动画中再点一下，View Transition 只能跳到终点再重来（不可打断）。

### 3. 每次开关面板，整页样式重算两次

`toggle-cost.mjs --why` 里每次开关都是"Invalidation set invalidates subtree"。两个原因：

- **33 条 `[class*="context"] [class*="track"]` 这类按类名子串匹配的选择器**（`theme.css` 里为早已不存在的上下文组件写的别名，加上 `thread-header.css`）。浏览器没法判断一个子串会不会命中后代，于是**任何元素的 class 一变，它下面整棵树都要重算**——不只是面板开关，跑任务时容器上的每次 class 变化都在付这笔钱。
- **停靠轨道宽度是挂在应用根节点上的、会继承的自定义属性**（`--loom-sidebar-track` 等），开关面板时它一变，全应用的元素都要重新解析。

结果：侧栏开关在点击时重算 ~760 个元素（文档一共 ~600 个，加上伪元素），动画结束移除状态类时再来一次 ~760；检查器 662 + 738。fixture 是一段很短的对话，长对话里这笔钱随页面一起变大。

### 4. 打开分两拍

右侧停靠面板挂载时直接以 90% 不透明出现，等出现过程（`useMotionPresence` 的"entering → 两帧后 entered"）走完才开始滑——先"啪"一下出来，~65ms 后再动。所有用出现过程的浮层、页面、折叠都晚两帧才开始入场；右键菜单还多等一个 `requestAnimationFrame` 才显示。

### 5. 按下：十四种写法，一百个控件没反应

静态有 65 条 `:active` 规则：缩放从 .88 到 .995、下移 1px、左移 1px、五个家族各自的按下底色，深浅主题还互相打架。实际跑起来（507 个控件）：图标按钮的图形会缩到 .88（`icon-motion.css`，这是一致的）；但**侧栏会话行、设置导航、菜单项、选项等 101 个控件按下去什么都不变**，几个卡片还会整张缩放（带字一起糊）。

### 6. 悬停上抬

~35 条规则让按钮、卡片在悬停时 `translateY(-1px)`（大多数已被后来的规则盖掉、成了死代码，少数还在生效）。带字的东西上下浮动 1px（150% 缩放下是 1.5 个设备像素），字会发虚，也违反了"带字的不动"。

### 7. 其他

- 会话右键菜单（侧栏）用关键帧进出，不能中途掉头。
- 已完成过程的折叠收起时，文字淡出有 64ms 的延迟、高度用软起步的 inout：点击后 ~70ms 什么都不动。
- 右侧快捷工具坞有一套自己的时长和弹簧（10-18 时故意留下的），"活着"的循环（状态点呼吸、运行中的会话、思考的扫光、骨架屏）有 1.3、1.4、1.5、1.8、1.9、2、2.2、2.4、2.6 秒九种节拍，同屏的几个状态点各跳各的。
- 七处代码各自判断"减少动态"，其中两处只认系统设置、不认应用内的开关。

## 三、改了什么

### 退出：第一帧就开始走

`--loom-ease-in` 换成 **`--loom-ease-exit: cubic-bezier(.3, .3, .55, 1)`**：第一个控制点在对角线上（匀速起步，第一帧就有变化），最后软着陆。全部退出改用它（浮层、菜单、页面、遮罩、toast、停靠面板）。浮层 Esc 后半透明的时间 ~106ms → ~55ms。

### 停靠面板：活的，有深度，走得干脆

- **只有侧栏截图**：检查器和右侧的出现式面板（代码审查、子代理、产物、项目、浏览器），以及后台任务这类不改左右停靠的变化（以前也走快照，只是什么都不截，白付一帧冻结和两次整页重算），都在真实元素上用 CSS transition 动，第二次点击会从当前画面上的位置掉头；对话内容由 FLIP 从旧位置滑到新位置（390ms，`move`）。右侧从来不走的 View Transition 规则（`loom-inspector`、`loom-*-surface` 的命名、关键帧和 `!important` 冻结）删除。
- **a61 的深度保留**：从自己那一侧来，8px 位移 + `scale(.992)`，原点在面板的那条边上，变换在 `panel` 步上用 `out` 曲线沉到位。
- **第一帧就读得清**：常驻的检查器（和没有快照时的侧栏）淡入用负延迟 `calc(var(--loom-dur-fast) * -.2)` 从曲线的 20% 处开始（`out` 曲线在那里正好是 .72）；右侧的出现式面板用 `@starting-style { opacity: .72 }`。和浮层同一个规矩：按需打开的东西，第一帧就是 .72。
- **离开走 fast-out**（120ms，`exit` 曲线）——a61 的旧快照本来就是 78–92ms 走掉的；去掉了把退出拉成 390ms 的 `!important`。关闭的检查器（和侧栏）在轨道收起的同一刻被绝对定位钉在原处（两条网格线都写上），在滑进来的对话上方淡出，走完再 `visibility: hidden`。
- 面板的出现过程保留时长改成 `RETAIN.fast`。

**侧栏保留它的 View Transition。** 这一轮先把侧栏也做成了活的（量到点击后 ~30ms 开始动、可以中途掉头），但同一天另一轮（`12ef709e`）在快照上做了工具栏按钮的连续移动：开关按钮停在指针下不动，羽毛笔在卡片的顶栏和头部之间滑过去，两者在交接时都不变淡，并有自己的测试（`sidebar-toolbar-motion-browser.mjs`）。活的卡片做不到"卡片淡入而里面的按钮始终不透明"，所以合并时侧栏回到快照，这一轮的其余改动都在它之上。快照的冻结帧有一部分是整页样式重算：这一轮去掉了三次里的一次（新状态的那次，从 ~730 个元素降到 ~270），另外两次是 View Transition 本身的代价（Chrome 为过渡插入、移除一张样式表，整页重算）；侧栏开始动的时间从点击后 ~85–110ms 降到 ~35–50ms。要再快，得把共享按钮的连续移动做成活的（第八节）。

### 出现：不再等两帧

`useMotionPresence` 重写：打开时直接挂载为 `entered`，入场从哪里开始写在样式表的 **`@starting-style`** 里，浏览器在元素第一次出现的那一帧就开始过渡；关闭时 `exiting`，**退出的计时从退出真正开始的那一帧算起**（在 `requestAnimationFrame` 里才开始计时），主线程忙的时候退出会晚开始，但不会被提前卸载打断。18 条 `[data-motion-phase="entering"]` 规则全部改成 `@starting-style`；`WeaveFlow` 的折叠用 `data-settled` 区分"历史里本来就开着"的和"现在才打开"的。
注意：`@starting-style` 没有额外的权重，必须写在它所起步的那条规则**之后**（设置页因为顺序反了一度直接出现，没有淡入），`test_motion_feel_contract.py` 会查。

右键菜单在 layout effect 里测量完就显示，不再多等一帧；侧栏的会话菜单从关键帧改成 transition + `@starting-style`，可以中途掉头。

### 样式重算：开关面板不再波及整页

- 33 条类名子串选择器换成它们今天真正命中的类（上下文表的芯片、轨道、行百分比、色块、说明、按钮、面板），每条保持原来的权重和声明；为不存在的旧组件写的 ~390 行别名删掉。两个主题、表盘开合、悬停三种状态下，上下文表所有部件的计算样式**逐项对比零差异**（`--context=1` 是给 live-app fixture 新加的参数，表头会显示上下文表）。
- `--loom-sidebar-track` / `--loom-inspector-track` 用 `@property … { inherits: false }` 注册，变化只留在外壳本身；拖动线直接读面板宽度。

结果（无头 Chrome，生产构建，一次开关碰到的元素 / 主线程中位数）：

| 开关 | 改之前 | 改之后 |
| --- | --- | --- |
| 侧栏收起（快照） | 759 + 731 + 767 个元素，90ms | 765 + 277 + 770 个，50ms（两次整页重算是 View Transition 自己的） |
| 侧栏（不用快照时） | 整页两次 | 197 + 264 个 |
| 检查器 | 662 + 738 个，80 / 62ms | 64 个，43 / 40ms |
| 代码审查面板 | 整页，80 / 72ms | 121 个，49 / 39ms |

### 按下：表面一层光，图形收一点，字不动

新文件 **`src/motion/press.css`** 是"按下"的唯一主人：所有按钮、`[role=button|menuitem|option|tab]`、`summary` 在 `:active` 时叠一层 `--loom-press-wash`（深色 `rgba(255,255,255,.055)`，浅色 `rgba(28,31,37,.06)`）作为背景图层——保留控件自己的底色，跟着它的圆角，按下立刻出现、抬起立刻消失。图标的图形照旧收到 `--loom-press-glyph`（.88）。**任何带字的控件不再缩放、不再位移**；各家族自己的按下底色（`--pl-press`、`--mp-press`、`--tc-press`、`--cq-press`、`--sr-press` 在 `:active` 上的用法）和 ~45 处 `:active` 位移 / 缩放删掉。保留的例外：设置开关的圆钮被按住时拉长（无字的标记）、窗口按钮自己的按下态、头像裁剪的抓手光标。不该有这层光的控件写 `data-press="none"`（窗口按钮、链接样的"查看洞察"）。这条规则不点名任何组件类，所以组件依旧只有一个样式表（`ui_stylesheet_ownership.py`）。

### 悬停：不抬起任何带字的东西

删掉 36 处带字控件的悬停位移（按钮、卡片、文件行、OAuth 按钮、"回到最新"按钮的上浮和放大、Markdown 图片提示的 2px 上浮等）；保留无字的标记（箭头、图形部件、伪元素的光）。

### 其他

- **过程折叠收起**：文字在 fast-out 上立刻淡出，高度照旧用 inout 软收。
- **快捷工具坞上了尺子**：它自己调好的滑块弹簧（zeta .86，0.5% 过冲）成了尺子上的 `--loom-spring-settle`，弹出用 `--loom-spring-pop`，滑入用 `glide`，时长都落在步上，错开是尺子的一半；`RightToolDock.tsx` 的 `SETTLE_MS`、键盘重排的弹簧都从 `motion/tokens.ts` 取。
- **循环的节拍**：`--loom-period-breathe` 1.8s（状态点、运行中的会话、头部状态、过程里的呼吸和光晕、工具坞的 ping）、`--loom-period-shimmer` 2.4s（思考 / 执行中的扫光，`--thinking-cycle` 也指向它）、`--loom-period-skeleton` 1.3s（骨架屏）、`--loom-period-spin` 900ms（转圈）。
- **一个"减少动态"的判断**：宠物、软指针、点击涟漪、跟随滚动、透镜、主题切换、门户光标都改用 `prefersReducedMotionNow()` / `useReducedMotion()`，监听用新的 `onReducedMotionChange()`；透镜和主题切换因此也开始尊重系统设置。
- **一个滑行**：侧栏的选中滑块和设置导航的轨道共用 `src/motion/glide.ts`（`translate` 走 glide 曲线、`scale` 在中段拉长，时长随距离 `travelMs`）。

## 四、改之后量到的（真实 Electron，165Hz，生产构建，前后各两轮交替）

从点击（或按键）算起的毫秒数。"开始"是第一帧变化，"一半""九成"是走到一半、九成。改之前一栏里侧栏的"开始"是 View Transition 开始动的时间。

| 交互 | 改之前 | 改之后 |
| --- | --- | --- |
| 浮层、菜单 Esc 关闭 | 开始 ~20–35，一半 ~100–113，九成 ~124–137 | 开始 ~13–17，一半 ~55–59，九成 ~98–102 |
| 消息右键菜单出现 | ~25–30 | ~1–5（第一帧就是 .72） |
| 过程展开 | ~35–40 才开始长高 | ~17–22 |
| 过程收起 | ~70–75 才开始动，九成 ~165 | ~10–13，九成 ~95 |
| 侧栏收起（快照） | 一帧冻结，~85–110 才开始动 | ~35–40 |
| 侧栏展开（快照） | ~60–80 | ~37–48 |
| 检查器打开 | ~35–70 才出现，一半 ~100–135 | 点击的那一帧（.72） |
| 检查器关闭 | ~135–150 才动，一半 ~335–350，九成 ~410–420 | 开始 ~15–20，一半 ~60，九成 ~100–105 |
| 代码审查关闭 | ~125–150 才动，一半 ~325–350，九成 ~390–420 | 开始 ~20，一半 ~63，九成 ~105 |
| 代码审查 / 子代理打开 | 第一帧 .9，~65 后才开始滑 | 第一帧 .72，同一帧开始沉到位 |
| 后台任务打开 / 关闭 | ~70–115 才出现 / 一半 ~165 | ~31 / 一半 ~75 |
| 设置关闭 | 一半 ~175–180 | 一半 ~117 |

设置第一次打开要 ~300ms，两边一样：那是设置页第一次渲染的成本，不是动效（见第八节）。
来回（动画中第二次点击）：检查器、审查面板都是一次平滑掉头，没有跳到终点再重来；侧栏的快照在第二次点击时由新的快照接手（`sidebar-toolbar-motion-browser.mjs` 测着）。

## 五、现在的动效体系（一张地图）

| 哪里 | 管什么 |
| --- | --- |
| `src/motion-tokens.css` | 时间（press 90 / fast 150·120 / base 240·200 / flow 320·260 / panel 390 / slow 520、stagger 40）、曲线（out / move / **exit** / inout / glide）、弹簧（**settle** / soft / lively / pop）、按下幅度、循环节拍、"减少动态"把时钟折成 1ms |
| `src/motion/tokens.ts` | 脚本里的同一套数字：`DUR` `DUR_OUT` `EASE` `SPRING` `RETAIN` `travelMs` |
| `src/motion/useMotionPresence.ts` | 出现 / 离开的生命周期（`@starting-style` 入场，退出按帧计时） |
| `src/motion/glide.ts` | 无字标记的滑行 |
| `src/motion/press.css` | 按下 |
| `src/motion/useReducedMotion.ts` | 唯一的"减少动态"判断和订阅 |
| `workspace-panels.css` / `panel-language.css`（`.pl-dock`、`.pl-reveal`） | 停靠面板的深度、原地出现；侧栏的快照（卡片 + 两个和头部共享的按钮） |
| `icon-motion.css` | 图标的手势 |

方言（每个表面只属于一种）：**停靠**（检查器、右侧面板：深度，第一帧 .72，fast-out 离开；侧栏是同样的深度，走快照，按钮在两个工具栏之间滑）· **原地**（对话框、页面、遮罩：只用透明度，base 进 base-out 出）· **浮层**（浮层、菜单、提示：第一帧 .72，fast 进 fast-out 出）· **折叠**（行高走布局）· **标记**（滑块、图标、珠子、状态点：glide 或弹簧）· **循环**（四个节拍）。

## 六、规矩（这一轮加的，`tests/test_motion_feel_contract.py` 守着）

1. 输入后的第一帧就要有变化。退出用 `exit` 曲线，没有 ease-in。
2. 按需打开的东西第一帧就读得清（.72），再沉到位。
3. 停靠面板是活的：能中途掉头；离开走 fast-out。只有侧栏截图，而且只截卡片和它与头部共享的两个按钮。
4. 出现用 `@starting-style`，不等两帧；它必须写在所起步的规则之后。
5. 按下：表面一层光（press.css），图形收一点；带字的不缩放不位移。
6. 悬停不抬起带字的东西。
7. 不用类名子串选择器；跟着开关变化的自定义属性不能继承到整棵树。
8. "活着"的循环共用四个节拍。

## 七、验证

- `tests/test_motion_feel_contract.py`（9 条，上面的规矩逐条读源码）——**15 种故意破坏全部变红**（`desktop-react` 外的脚本把规则一条条弄坏再跑，再恢复）。`tests/test_motion_scale_contract.py` 改成新尺子：exit 曲线、settle 弹簧、工具坞和停靠面板不再豁免、"减少动态"副本必须为零。
- 改了断言的旧契约（意图不变，钉的是旧实现）：`test_panel_enter_motion_contract.py`（右侧停靠面板是活的，侧栏的快照只截卡片和共享按钮）、`test_panel_layout_motion_contract.py`、`test_global_motion_contract.py`、`test_panel_language_contract.py`、`test_context_meter_motion_contract.py`、`test_capsule_motion_contract.py`、`test_run_progress_fade_contract.py`、`test_settings_navigation_motion_contract.py`、`test_task_flow_motion_contract.py`、`test_task_sheen_contract.py`、`test_thinking_glyph_contract.py`。Python 全量（和合并前的 HEAD 副本对比）：没有新的红；HEAD 上红着的 4 个面板契约（另一轮改了侧栏快照后没跟上）现在绿了；剩下的红和本改动无关（浏览器后端、PTY 是环境原因，`test_design_scale_contract.py` 是另一轮新加的 7 处低于 11.5px 的字号）。
- Node 静态测试：只剩 `model-access-ui`、`model-policy-catalog` 两个红（需要先构建 `dist-electron`）；`icon-motion.test.mjs` 的"转圈共用一个节奏"在 HEAD 上是红的（侧栏运行中的圆环写了 `1.8s`），现在它走 `--loom-period-breathe`，测试也认这个节拍，绿了。
- 浏览器测试：全部 60 个在合并前的 HEAD 副本和合并后各跑一遍。合并后：`motion-language-browser.mjs` 改成新语言（exit 曲线、会话菜单是 transition、停靠面板第一帧可读 / fast-out 离开、只有侧栏截图而且只截卡片和两个共享按钮、工具坞是标记；深色、浅色、减少动态三轮）；`right-tool-dock-motion-browser.mjs` 的"工具坞进场"改成读起始样式（挂载即 entered，第一帧仍隐藏、随后沉到位）；`runtime-motion-browser.mjs` 的"换了一个表面就有自己的入场"改成"换了表面就是新元素"（fixture 给 `key`）；`browser-sidebar-browser.mjs` 改读交接方向（`data-loom-panel-handoff`）并断言没有快照——它在 HEAD 上是红的，现在绿了；另一轮的 `sidebar-toolbar-motion-browser.mjs` 绿。其余结果两边完全一样：component-surfaces、disclosure-motion、earlier-process、execution-sequence、progress-feedback、project-send、readability-floor、right-tool-dock-browser、runtime-effects、weave-flow、workspace-visual 在 HEAD 上本来就红，与这次改动无关。
- 真实 Electron 窗口量的数见第四节；按下的截图对照（深浅两套）用 `press-sheet.mjs`。

## 八、没做 / 可以继续

（2026-10-22：侧栏的快照、首次打开的渲染成本、窄窗口里输入框的跳变、原生 `title` 提示都已处理，见 `docs/switching-continuity-2026-10-22.md`。）

- **侧栏的快照**：工具栏按钮的连续移动现在靠 View Transition，代价是点击后 ~35–50ms 才开始动、动画开始和结束时各一次整页样式重算、中途不能平滑掉头。可以把卡片做成活的（这一轮已经做过、量过），共享按钮另起一层用 FLIP 在两个位置之间移动（卡片淡入时按钮不跟着变淡），就能两者兼得。
- **首次打开的渲染成本**：设置、检查器、代码审查面板第一次打开时，第一帧要 40–100ms 渲染内容。这不是动效问题（现在面板第一帧就可见，但第一帧本身晚）；可以先画外壳、内容推迟一帧。
- 右侧有面板时开关侧栏：输入框的宽度会瞬间变化（FLIP 只能平移，不能无畸变地改宽度），左边缘会跳一下。之前就如此。
- 179 个原生 `title` 提示：出现慢（系统延迟）、样式和工具坞的名字提示不一致。
- 没有在用户安装的那份应用里直接观察；量的是同一套代码的生产构建在同一台机器的 Electron 里。
