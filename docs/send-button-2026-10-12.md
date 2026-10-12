# 发送键：圆盘里的纸飞机（2026-10-12）

起因：用户给了一张图（浅灰圆盘里一只描边纸飞机），说“把发送按键做成这种按钮图标，并配上动画”。
范围是输入框的**发送键**（空闲输入框 `ComposerBase.tsx` 和任务进行中的补充输入框 `Composer.tsx` 各一个）和它交给的**停止键**；图标动效仍然归 `icon-motion.css`，皮肤归 `composer-refined.css`。

## 做了什么

### 形

- 两个键都是 36px 圆盘（`composer-refined.css` 里 `border-radius: 50%`），停止键同一个类，所以也是圆的。
- 图标从向上箭头换成已有的 `Send`（纸飞机，17px，线宽 2），两个输入框都换了。`ArrowUp` 字形没删（它是图标模块里和 lucide 同名的一份），只是界面里没人再用。
- 空闲（没有可发的内容）就是图里的样子：浅灰盘 `#f2f3f5`、灰色飞机；有内容时沿用原来的“唯一的主操作”：浅色主题黑盘白飞机，深色主题亮盘暗飞机。没有改颜色。

### 飞机放在哪（`icon-motion.css`）

纸飞机的轮廓在 24 格里居中，**墨迹重心不居中**：按像素量，重心在 (13.83, 10.12)，偏右上 1.8 / 1.9 个单位。按外框居中的飞机放进圆盘会显得被机头推向右上。所以姿态是三档，都在 CSS 像素里（17px 图标）：

| 状态 | 飞机相对盘心 | 说明 |
| --- | --- | --- |
| 空闲 | (−1.3, +1.3) | 按重心居中，安静 |
| 有内容 | (−0.5, +0.5) | 沿机头方向前进约一个线宽，像被盘子“上了膛”；用悬停同一条弹簧过去 |
| 指向 | (+0.63, −0.63) | 原有的“发送”倾斜（`--ic-lean`，1.6 个单位），没改 |

按下仍是图标缩到 .88。这三档是 `translate` 过渡（不是动画），所以不会因为别的规则切换而重播；`scripts/tests/send-button-browser.mjs` 读几何位置核对。

### 发送时（真正的演出）

发送后整个输入框在 280ms 内交叉淡入成补充输入框（`Composer.tsx` 的 `composer-mode-crossfade`），旧的那份留作一个副本淡出。动作都挂在这个副本和新来的那份上：

- **起飞**（旧发送键，只有 `is-ready`，即“有内容可发”的才起飞）：先后缩 1.4px（17ms），再沿机头方向加速飞出圆盘（78ms，`overflow: hidden` 只加在这个副本上，所以飞机从盘沿穿出去）。副本整体用 `ease` 淡出，到 100ms 已经只剩三成，所以起飞只能这么快才看得见。
- **降落**（新来的发送键）：90ms 后飞机从左下方进盘；**停止键的方块**也等 80ms 才从盘中心长出来（`li-grow` 现在带透明度），否则它会透过正在淡出的旧盘叠在飞机上。
- **运行结束，停止 → 空闲**：停止方块缩小，飞机降落，**不起飞**（那时副本里的发送键是 `is-idle`，没有东西离开）。
- **家里发第一条**（要先建对话，`submitting` 期间键是禁用的，实测时在页面里给 `thread/start` 加了 1.5s 延迟）：等待时键是 `is-ready` 但禁用，样子和空闲一致（浅灰盘、灰飞机、“Sending…”）；`running` 翻转时副本里的键仍是 `is-ready`，所以照样起飞。

### 补充要求（键不换，飞机换）

补充输入框的发送键一直在，所以飞机自己起飞再回来：`Composer.tsx` 用 `launched`（已发出的条数）给飞机做 `key`，每条新建一个 `<svg>`，`li-relaunch`（760ms）从头播：起飞约 100ms，空盘约 100ms，新飞机从左下方带弹簧回来。键上有 `data-sent`（给 `overflow: hidden` 用）。

### 减少动态

系统级和应用设置两种方式下：姿态仍然取（那是状态），但没有起飞 / 降落 / 长出 / 再起飞——副本本来就不存在（`Crossfade` 在减少动态下不渲染），`li-relaunch` 在两层守卫之后（`@media (prefers-reduced-motion: no-preference)` 和 `:root:not([data-loom-reduced-motion="true"])`）。

## 顺带修的

- **补充输入框的发送键没有 `is-ready` / `is-idle`**，所以空着的时候是“更淡的盘 + 更深的飞机”，和空闲输入框两种样子。现在两个输入框同一套类；`composer-interactions.css` 里“禁用的发送键”的三条选择器从 `.is-idle:disabled` 放宽到 `:disabled:not(.stop)`（同时覆盖“正在发送”那一小段）。为此 `canGuide` 取代了行内的 `disabled` 表达式，`test_live_steering_ui_contract.py` 里按源码文本断言的那一行跟着改了。
- **空闲 ↔ 有内容时阴影不插值**：空闲的阴影只有一条内阴影，有内容的有三条（外阴影、描边、内阴影），形状不同的列表不能插值，有内容的那一整套在第一帧就到位——深色主题里盘子还是暗的，上沿先亮起一道 .82 的白边（逐帧里看到的，`getComputedStyle` 在 t=0 就已经是有内容的值）。现在空闲的列表补齐同样的条数（外阴影全透明），阴影和盘子一起渐变。
- **键盘聚焦在发送键上什么都看不见**：`composer-interactions.css` 画的 box-shadow 聚焦环被 ready / idle 的阴影规则压住（特异性更低），而 outline 又是 `none`。现在 `composer-refined.css` 里给 `.send-button:focus-visible` 一个 2px 强调色 outline，离盘沿 2px。
- `scripts/fixtures/live-app.tsx` 补了 `queryModelUsage` 桩：额度环（`ModelUsageIndicator`）读 `windows[0]`，夹具的万能桩返回 `{}`，整个输入框渲染时崩。这是验证这次改动的前提，不是改动本身。

## 验证

- **真实渲染逐帧**（150% 缩放，放大不平滑；暂停动画再定位时间，另外把 React 的 280ms 副本计时器按住，否则取到第三帧副本已经没了）：发送 / 停止两个方向的交接、有内容 / 悬停 / 离开 / 按下 / 删空的姿态、补充要求的再起飞，浅色深色各看一遍。第一版起飞要 168ms 才飞出盘沿，那时整块输入框早已淡没了，按帧改成现在的 78ms。
- **新测试** `scripts/tests/send-button-browser.mjs`（真实输入框）：圆盘和飞机、三档姿态（读几何）、悬停倾斜、发送时副本里播什么（`li-takeoff` / `li-land` / `li-grow`）、副本的 `overflow`、停止时不起飞、补充要求每条都换新飞机并重播、减少动态两种方式下没有任何飞行动画。**突变检查**：10 个突变（盘变回方的、姿态改掉、起飞 / 降落 / 裁切去掉、结束时也起飞、补充输入框的 key / 类去掉、减少动态的守卫去掉）全部被抓到。
- `icon-motion.test.mjs` +1（样式里发送相关的关键帧都存在且被用、再起飞在两层守卫之后、两个输入框都画 `Send`、补充输入框的 key 和 `data-sent`），`test_composer_refined_contract.py` +1（圆盘；禁用样式放宽）。
- 已有测试：`icon-motion-browser.mjs`、`user-send-browser.mjs`、`project-send-browser.mjs` 通过；`tsc --noEmit` 通过；Python UI 契约测试和 `scripts/tests/*.test.mjs` 里**这次没有新增失败**。仍红的几条在改动前的 `HEAD` 上就红，和这次无关：
  - `test_global_motion_contract.py::test_transient_surfaces_have_symmetric_presence`（表情面板挪走后 `data-motion-phase` 只剩 2 处，断言还是 ≥3）；
  - `composer-signin-draft.test.mjs`（`submittingRef is not defined`）、`conversation-home-navigation.test.mjs`（`modelSwitchTasks is not defined`）：测试用 `eval` 取出 `submit` / `send`，源码里新加的引用它的桩里没有；
  - `icon-motion.test.mjs` 的两条（`model-usage.css` 自己定义了 `model-usage-spin`，1.5s，违反“只有 `icon-motion.css` 定义转圈关键帧、同一节奏”）；
  - `workspace-visual-browser.mjs`、`control-popover-response-browser.mjs`（后者在 CI 里）：`workspace-visual.html` 夹具同样的万能桩让额度环崩了，页面根本没渲染输入框。

## 没做 / 已知

- **没在 Electron 真机和 165Hz 显示器上看**；无头 Chrome 取的是精确帧，节奏的“手感”没法替代真机。
- 起飞受 280ms 的整块交叉淡入约束：想让飞行更舒展，要改的是输入框切换本身的时长，不是飞机。
- 家里发第一条时，从点击到起飞之间（建对话那一段）键是禁用的灰盘，飞机在“空闲”姿态；之后才起飞。没有为这一段另加动作。
- `composer-interactions.css` 里 `.has-content .send-button.is-ready::before` 的淡紫光晕规则还在；截图对比看不出它画了什么，没动。
- 夹具的万能桩（`{}`）会让任何读结构化返回值的组件崩；`ModelUsageIndicator` 自己对空返回做个兜底，比逐个夹具补桩更稳，这次没改它。
