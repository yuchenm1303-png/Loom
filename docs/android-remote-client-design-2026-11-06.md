# Loom 手机客户端设计

日期：2026-11-06（第三版）。状态：阶段 0、1 已实现，见第 11 节。
界面稿：<https://claude.ai/artifact/XwnVUgNLoVd9cHSgALzg4C>
测试机：华为 Mate 50，用户确认可装 APK，按安卓开发。

第二版的变化：目标从「窄权限遥控器」改为**界面不同、功能尽量与电脑端一致**。第一版的三个结论因此作废：手机专用的精简操作表、Host 投影后的缩略内容、手机只能「允许一次」。技术选型也从纯原生改为共享逻辑层 + 薄原生壳。

## 1. 定位

**你在手机上发一条消息，电脑上的 Loom 照常工作，就像在电脑上打字一样。** 手机不运行任何工具：Agent、文件、命令和模型调用全部在电脑上。手机只做三件事：把你的话传过去，把结果显示出来，在需要时让你批准。

第 2 节的方法列表是手机界面上按钮对应的请求，不是手机要执行的工作。

硬性要求：

- **模型消息完整显示。** 手机与电脑读同一份 `thread/read` 数据，不做缩略。长代码块、表格横向滚动，工具输出和 diff 可全屏查看。
- **审批与电脑一致。** 四种决定都有：允许一次、本次对话都允许、这个项目以后都允许、拒绝（可带原因）。每个对话能切权限模式，保存的规则能查看和删除。

沿用的规则：手机只操作用户选中的那台电脑，离线绝不回退到另一台。

## 2. 功能对齐

电脑端 renderer 共调用 62 个 App Server RPC。这些方法加上 `desktopOperations` 按下表分级。分级在 Host 执行，gateway 白名单只是第二道防线。

| 级别 | 内容 |
| --- | --- |
| 直接可用 | 对话全部读写（新建、恢复、分叉、重命名、归档）、`turn/*`、`approval/respond` 的四种决定、切模型和推理强度、切到只读/需确认/工作区、项目、建造（`projectRun/*`、`projectWeave/*`）、文件读取、附件、终端读取、设置读取 |
| 需指纹 | 切到完全访问、「这个项目以后都允许」、删除规则、删除对话、终端输入、`settings/set`、`mcp/manage`、`plugin/manage` |
| 暂不开放 | 模型密钥（`setModelProviderKey`、带密钥的 `addModel`、`discoverModels`）、账号改动、`readClipboardText`、导出日志 |
| 需换形态 | `pickDirectory`（会在电脑上弹窗）改为手机端远程目录树；`revealPath`、`openExternal` 改为在手机上预览，或显式提示「在电脑上打开」；内嵌浏览器和 Computer Use 只显示截图、状态与审批，不做实时画面 |

暂不开放的密钥类操作，是因为 v1 内容会明文经过中继。端到端加密上线后可以放开。

## 3. 架构

```text
手机
├ 移动界面（React）   desktop-react/src/mobile/，新 Vite 入口
├ 共享逻辑            useLoomCore、approvalProtocol、MarkdownMessage 等
├ installMobileBridge 实现 window.loom
└ 原生壳              android/，持有 socket、token、私钥
     │ WSS /api/ws/client?device=<id>，Bearer
     ▼
loom_web_gateway（转发 + 方法白名单）
     ▲ WSS 外连，已有
Loom Host（方法分级 + 验签）── App Server
```

### 为什么改成共享逻辑层

纯原生意味着用 Kotlin 重写 62 个 RPC 的调用逻辑、Markdown 渲染和审批协议，电脑端每加一个功能手机就落后一次，这正是用户要避免的。

已核实可行：`useLoomCore.ts` 只依赖 React 和 `state/` 下的小模块，通信全部经过 `window.loom`。手机只需实现这个桥并写新界面。`App.tsx` 不复用，桌面布局不搬到手机上。

### 原生壳的职责

壳只做 WebView 做不好的事，内容从 App 内打包资源加载，不加载远程代码：

1. WSS 连接：放在前台服务，token 不进 JS，界面被回收时连接还在。
2. Keystore 加密保存 session。
3. 指纹确认。
4. 通知和推送。
5. 扫码、文件选择、相机、麦克风。

所以换一种外壳的工作量可控，界面和逻辑不用重写。

### 改动点

- **Account**：新增 `mobile` session，单独列出、可吊销，不能换模型凭据。
- **Gateway**：新增 `/api/ws/client`，Bearer 认证，不校验 Origin。其余沿用 32 并发、120 秒超时和 30 秒身份复查。
- **Host**：手机走 `call`，由 `electron/mobileAccess.ts` 按白名单过滤。网关只接受协议版本够新的 Host，旧 Host 收不到手机请求。
- **不用 `RemoteControlPolicy`**：它只允许控制只读和需确认模式，且不支持长期授权，与功能对齐冲突。陈旧审批由 App Server 拒绝（`staleResponses: rejected`）。

## 4. 安全模型

功能放开后，手机丢失的风险随之上升，所以用三层控制，尽量不增加点击：

- **配对**：电脑显示二维码，手机扫码并生成密钥，双方核对 6 位数字，**在电脑上点允许**后才发 session。
- **session 类型**：`mobile` 由账号服务签发，网关和 Host 据此限制操作，手机自己声明不算数。账号服务对手机 token 默认拒绝，只放行 3 个路径。
- **指纹分级**：只有第 2 节「需指纹」的操作需要验证，「允许一次」「本次对话都允许」不要求指纹。

此外：电脑端可吊销手机，30 秒内断开；App 可选启动解锁；审批页设 `FLAG_SECURE`。

v1 内容明文经过中继，与现有网页端相同，隐私说明要写清楚。端到端加密是后续项。

## 5. 连接与状态

**快照是真相，事件流只是加速。** 回前台、重连或收到通知时先取快照，再接事件。现有逻辑已有 `thread/read` 重取路径，gateway 不加持久化。

- gateway 单实例，自动部署时会断线，客户端要当常态处理。
- 写操作带幂等键，不确定是否送达时用同一键重试。
- 离线缓存只读，标明「离线前看到的」，审批入口不可用。
- 大载荷分段：`thread/read` 沿用 `turnLimit` 窗口，大文件和工具输出按需加载，不截断。
- `item/delta` 高频流要在 Mate 50 上实测渲染性能。

## 6. Mate 50 与通知

这台手机的前提：没有 Google 服务，FCM 不可用。

通知分两步：

| 阶段 | 方案 | 待核实 |
| --- | --- | --- |
| v1 | 有任务运行时前台服务保持连接，显示常驻通知 | 系统省电策略，需要引导用户把 Loom 设为手动管理启动 |
| v2 | 华为 Push Kit 只发「有事件」信号，App 醒来后经自己的连接取内容 | 是否需要开发者认证和上架、消息分类配额 |

内容不经过推送厂商，通知由 App 在本地生成，所以可以显示命令并提供「允许一次」按钮，解锁后才能操作。

## 7. 技术栈

| 模块 | 选型 |
| --- | --- |
| 界面 | React 19，与 `desktop-react` 共用构建，新入口 `mobile.html` |
| Android 壳 | Kotlin + WebView（`WebViewAssetLoader`），WebView 版本要在 Mate 50 上确认 |
| 网络 | OkHttp WebSocket，放前台服务 |
| 密钥 | Android Keystore + BiometricPrompt |
| 扫码 | 离线条码库，不依赖 Google Play 服务 |
| 版本 | minSdk 29，targetSdk 36 |

本机已有 Android SDK 36 和 JDK 17。视觉沿用 `--loom-workspace-*` 令牌，琥珀色只表示「需要你」，界面双语。

## 8. 分期

| 阶段 | 内容 | 完成标准 |
| --- | --- | --- |
| 0 通道 | `mobile` session、`/api/ws/client`、方法白名单、电脑端配对页 | 测试：越级操作、手机 token 冒充 Host 或浏览器全部被拒 |
| 1 对话 | 壳、`installMobileBridge`、现在页、完整对话、四种审批、权限和模型切换、停止、插话 | Mate 50 上：长消息完整显示，切网后状态恢复 |
| 2 全功能 | 建造、文件、改动与 Git、终端、连接器、设置 | 第 2 节表格逐项打勾 |
| 3 通知 | 前台服务和常驻通知，然后接 Push Kit | 锁屏 1 小时后仍能收到审批提醒 |
| 4 增强 | 端到端加密（放开密钥操作）、语音输入 | 按需拆分 |

建议先做阶段 0 和 1。

## 9. 开放决定

1. **发行方式**：先在 GitHub Release 发 APK 自用；上架要先定 Push Kit 和资质。
2. **v1 明文经过中继**：与网页端相同。

## 10. 验证要点

- 权限：越级方法、手机 token 直连 `/api/ws/browser` 或 `/api/ws/device` 全部失败。
- 完整性：用真实长对话对比手机和电脑的消息内容，不允许丢失；含超长代码行、宽表格、图片。
- 网络：弱网、切 Wi-Fi/蜂窝、gateway 重启、Host 重启。
- 延迟和性能都在 Mate 50 上实测，电脑走代理的数字不能代替。

## 11. 实现状态（2026-11-06）

为什么去掉第二版的帧签名：网页通道本身没有签名，被攻破的网关可以直接伪装成浏览器，手机签名挡不住这条路。要防中继，需要所有客户端一起做端到端方案，放在阶段 4。

| 部分 | 位置 | 状态 |
| --- | --- | --- |
| 配对与 `mobile` session | `services/loom_account/server.py` | 已实现，有测试 |
| 手机通道 | `services/loom_web_gateway/app.py` | 已实现，有测试 |
| Host 白名单 | `desktop-react/electron/mobileAccess.ts`、`remoteRelay.ts` | 已实现，有测试 |
| 电脑端配对页 | 设置 › 手机，`MobilePairingPanel.tsx` | 已实现 |
| 手机界面 | `desktop-react/src/mobile/` | 用假壳在浏览器验证 |
| Android 壳 | `android/` | 可编译出 APK，构建见第 12 节 |

当前白名单比第 2 节窄，只含对话、审批、模型和只读项目信息。终端、设置修改、MCP 和插件管理还没开放，随阶段 2 的界面一起加。

指纹是手机本地的确认，防的是别人拿到解锁的手机，不防 token 被盗。

配对码目前是一次性内存票据，账号服务重启后需要重新生成二维码。

## 12. 构建

```bash
cd desktop-react && npm run build:mobile
```

```bash
cd android && ./gradlew assembleDebug
```

APK 在 `android/app/build/outputs/apk/debug/`，目前用调试签名。在非 ASCII 路径的机器上构建时，把 `GRADLE_USER_HOME` 设到 ASCII 路径。

调试页面：`scripts/fixtures/mobile.html` 用假的原生壳运行手机界面，可在浏览器里测试。
