# 输入框的面板：一个家族（2026-10-22）

起因：用户截了模型面板（模型名 + 蓝色发光的推理滑块）说"帮我仔细重做一下输入框这些的面板吧，统一动效和ui，原来的都不太好看，你有最高权限可以随便按你的审美改代码"。

## 一、原来是什么样

输入框工具栏上能打开五个面板：工具与技能、权限、模型（模型卡 → 模型库 → 某个供应商 → 添加连接 / 自定义模型 ID 两张表单）、余额与额度、发送前预览。它们是五套设计，叠在十张样式表上：

- **五种头部**：权限是"图标块 + 标题 + 副标题 + 右侧 Thread"，模型卡是居中的大模型名，模型库是"返回 + 标题 + 计数"，工具是"标题 + ×"，额度是 16px/700 的大标题 + 文字按钮"刷新"，预览又是"标题 + ×"。标题字号 12、13、14、15、16 都有，字重有 700（不在产品的四个字重里）。
- **三个面板没有进出场**：工具、额度、预览是直接挂载 / 卸载的，另外两个是 .72→1 的淡入。
- **颜色各说各的**：推理滑块是蓝色发光的珠子，带彗星尾、光晕、爆裂和轨道（产品的强调色是紫色）；工具面板是原生紫色复选框、紫色实心的 Auto 按钮、搜索框双重焦点框；供应商列表里当前模型是紫色底，权限列表里是灰色底。
- **低于可读下限的字**：工具面板的计数 8px、说明 10px、分组 11px。
- **各自监听**：每个面板自己往 document 上挂 pointerdown / keydown，关闭和焦点归还各写各的；模型库还有自己的一套方向键逻辑。
- 样式层层覆盖：`model-picker.css`（123 条规则）、`model-core-redesign.css`、`reasoning-thread.css`、`permission-popover-polish.css`、`model-panel-overrides.css`、`model-panel.css`（44 条早就没人用的规则）、`composer-tool-selector.css`、`model-usage.css`，再加 `composer.css`、`composer-refined.css`、`theme.css` 里的副本。

## 二、现在：一个套件，一张样式表

- **`src/components/composerPanel.tsx`** 是套件：
  - `useComposerPanelHost` / `ComposerPanelHost`：每个输入框一个"主人"，知道哪个面板开着。开一个就关掉另一个；在面板外按下或按 Escape 关闭；Escape 和做出选择后焦点回到打开它的按钮；面板在屏幕上（包括离场的那 120ms）时输入框带 `has-panel`，工作区带 `data-composer-popover`（让"回到最新"按钮让位）。实时引导输入框把自己的主人借给共享的控件，所以那里的预览和权限 / 模型也互斥。
  - `useComposerPanel(id)`：一个面板的状态（open、presence 的 phase、anchor / trigger 的 ref、toggle / close）。
  - `ComposerPanel`：外壳。挂在触发按钮上方 10px，左边与按钮对齐（预览是右对齐），被推回窗口内；高度跟内容走，最高到按钮上方的空间，超出时只滚动正文。用键盘打开时焦点进到当前那一行；上下键、Home / End 在行之间走，从第一行往上回到搜索框。
  - `PanelHead`（标题 13/600 + 末尾的元信息或图标按钮 + 可选的返回）、`PanelLabel`、`PanelFoot`、`Segmented`（一块滑块走到选中的那一项）。
- **`src/components/composer-panels.css`** 是这个家族唯一的样式表，前缀 `.cp-*`：
  - 外壳：输入框面板的底色、一根发丝线、浮层阴影（`--pl-shadow-float`）、13px 圆角（`--pl-radius-float`），和右键菜单是同一种浮层。
  - 解剖：头 48px，文字从里边缘 16px 开始（模型卡从品牌标记开始）；正文的行 36px 起、8px 圆角、悬停是一层淡色、选中是稍深一层；底部是 12px 的说明，在一根更淡的发丝线下面。
  - 颜色只有一种意思：**强调色 = 你当前的选择**（对勾、勾选框、推理的填充、"推荐"这个词、自选工具的数量）；警示色 = 完全访问和额度快用完；红色 = 出错。标签是字，不是药丸。
  - 字号只用 11.5 / 12 / 13，字重 400 / 500 / 600。
- **模型卡**：第一行是"品牌标记 + 模型名 + 供应商 + 更换 ›"，整行可点进模型库；下面是推理。推理重画成一根安静的轨道：每档一个点，填充到选中的那档，一个白色旋钮；悬停某一档，下面先读出它的说明。没有彗星、光晕、爆裂和轨道环了。中文界面里档位名和说明是中文。
- **工具与技能**：模式是"自动 / 自选 / 关闭"三段（原来是 Auto 加两个动作按钮）；自动模式下勾选框是中性灰（工具是模型自己选的），点任何一个就变成自选、变成紫色；搜索框和"工具 / 技能"切换在同一行；每个工具一行说明。原来的"全选 / 全不选"由"自选 / 关闭"代替，每组前面的勾选框仍可整组开关。刷新目录时保留原来的列表，按钮不会闪成禁用。
- **余额与额度**：标题 + 供应商 + 刷新图标；每个窗口是"名称 + 剩余" + 一根 4px 的条（低于 20% 时是警示色）+ "2 小时 24 分钟后重置"（一天以上写星期几和时间，悬停看完整时间）；说明在条下面，底部只写更新时间。工具栏上的额度环改成中性墨色，贴在模型按钮右边。
- **预览**：标题 + "尚未发送"，正文在一块浅底里按原样换行，附件是小标签。
- 没有关闭按钮：触发按钮本身开关，Escape 和点外面关闭，五个面板一样。

## 三、动效

五个面板一种方言，就是浮层那一种（`docs/motion-language-2026-10-18.md`、`docs/motion-feel-2026-10-21.md`）：

- 出现：第一帧就是 .72 的不透明度（立刻可读），150ms 的 out 曲线到 1；面板本身不位移、不缩放。
- 离开：120ms 的 exit 曲线到 0，离场时 `inert`、不接收输入；`RETAIN.fast` 之后卸载。再点一次会从当前的透明度掉头。
- 面板里换视图（模型卡 → 模型库 → 供应商 → 表单）或切换"工具 / 技能"：新的视图从 .72 淡入，和面板出现一样。
- 只有标记会移动：推理旋钮和填充、分段控件的滑块，用 `--loom-spring-settle` 走 `--loom-dur-flow`（320ms）；额度条用 slow 步长填充。
- 量过（无头 Chrome，逐帧）：五个面板第一帧都是 0.72、transform 为 none，过渡都是 `opacity 150ms cubic-bezier(.16,.78,.18,1)`；离场 120ms 降到 0，约 190ms 卸载，焦点回到按钮；旋钮和滑块的过渡都是 320ms 的弹簧。

## 四、顺手修掉的

- `composer-compact.css` 的按钮字重 450 不在四个字重里（`test_design_scale_contract` 和 `font-warmup.test.mjs` 都是红的）→ 500；输入框下面的提示 10.5px → 11.5px（可读下限）。
- 展开编辑区原来用 `.composer:has(.composer-editor-toggle[aria-pressed="true"]) textarea`，每次输入框里有变化都要重算 → 预览组件在输入框上写 `data-editor-expanded`。
- `renderer-styles.ts` 里 `composer-compact.css` 被排到了 `send-flight.css` 后面（`send-flight.test.mjs` 要求后者在最后）→ 移到 `press.css` 前面。
- 浅色主题 `theme.css` 里的旧规则：控件锚点 32px 高（按钮是 28px，面板因此在浅色下离按钮 12px、深色下 10px）、打开的按钮图标被画成紫色 → 删掉。
- 两个过期的测试断言（`loom.send(input, attachments)` 已经带上 `toolSelection`）。
- 低于 11.5px 的声明从 351 降到 303，`BELOW_THE_FLOOR` 跟着降到 303。

## 五、测试

- `tests/test_composer_panels_contract.py`：五个面板都用同一个外壳；只有一个主人在监听；只有 `composer-panels.css` 能写 `.cp-*`，退役的样式表和类名不再出现在任何样式表里；外壳是浮层的材质、没有渐变 / 毛玻璃 / 内高光 / `:has()`；进出场的写法；只有标记会走；强调色只出现在"当前选择"的那几处；推理轨道没有装饰层；字号和字重。
- `desktop-react/scripts/tests/composer-panels-browser.mjs`：两种主题下逐个打开五个面板——第一帧可读且在淡入（150ms / out 曲线）、不位移、立刻可交互；材质、标题字号字重、16px 起始位置五个一致；都挂在按钮上方 10px、在窗口内；Escape 后离场时 inert、卸载、焦点回到按钮；不经过指针地依次打开五个面板，每次只有一个开着；打开的按钮图标是正文墨色；推理旋钮用 320ms 的弹簧走到第四档；键盘打开权限面板时焦点在当前行，上键和 Home 能走。
- 变异检查（在镜像里一次破坏一处，14 处都让测试变红）：去掉入场、离场变瞬间、一个面板换圆角、一个标题变粗、外壳入场时位移、一个面板用自己的主人、Escape 不还焦点、旋钮跳变、没有方向键、打开的图标变紫、别的样式表写 `.cp-row`、强调色漏到标签上、面板自己监听 pointerdown、"自选"包含不可用的工具。
- 改过的旧测试：`test_composer_refined_contract.py`（模型卡例外删掉，面板不再属于这一层）、`test_panel_language_contract.py`、`test_global_motion_contract.py`、`test_live_steering_ui_contract.py`、`test_desktop_error_recovery_contract.py`、`test_design_scale_contract.py`、`composer-toolbar-ui.test.mjs`（没有关闭按钮了，模式是"自动 / 自选 / 关闭"）、`model-usage-browser.mjs`（面板有离场了）、`motion-language-browser.mjs`（外壳是 `section.cp`）、`model-access-ui.test.mjs`（`cp-primary`）。
- `live-app` fixture 多了 `?models=rich`（三个供应商、一个自定义连接、最近使用、五档推理）、`?usage=1`（两个额度窗口）和 `tools/catalog`。

## 六、没做的

- 线程标题栏里的上下文面板（`context-meter-panel`）不在输入框上，没有并进来；它已经用同一种浮层方言。
- 模型卡里较长的提示（同步状态、Loom Admin 策略、OpenCode 免费模型限制）仍是英文，和原来一样。
- 运行中的引导输入框在任务结束时整个换回普通输入框，开着的面板会随之消失（原来也是这样）。
