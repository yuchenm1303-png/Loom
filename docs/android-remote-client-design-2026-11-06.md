# Loom Android 远程控制客户端设计

日期：2026-11-06。状态：设计稿，尚未实现。
界面稿：<https://claude.ai/artifact/XwnVUgNLoVd9cHSgALzg4C>

## 1. 定位

手机不是第二个 Loom，而是电脑上那个 Host 的遥控器。Agent、文件、工具、模型调用都留在电脑上。

手机只做五件事：

1. 看：哪台电脑上有什么在跑、卡在哪。
2. 批准或拒绝。
3. 停止。
4. 插话。
5. 发一个新任务。

不做：编辑文件、改模型和密钥、改账号、完整 diff 审阅、Browser/Computer Use 实时画面。这些场景提供「在网页打开」跳到 `loom.smirel.com`。

沿用已有产品规则：手机只操作用户明确选中的那台电脑，选中的电脑离线时绝不回退到另一台（`services/loom_web_gateway/README.md`）。

## 2. 现状：哪些能复用，哪些不行

| 已有 | 位置 | 对手机的意义 |
| --- | --- | --- |
| Host 主动外连的中继 | `desktop-react/electron/remoteRelay.ts`、`services/loom_web_gateway/app.py` | 复用。电脑不用开端口 |
| 多设备路由，不串台 | gateway `RelayHub` | 复用 |
| 配对票据，120 秒、一次性 | `services/loom_account/server.py` `issue_device_pair`/`exchange_device_pair` | 复用机制，但换出的是**完整账号 session**，要新增受限类型 |
| 审批指纹、幂等、权限上限 | `app/remote_control/`（目前给 ChatGPT MCP 用） | 直接复用，这是手机控制面的正确内核 |
| 精简线程读取 | `thread/read`，`presentationOnly` + `turnLimit` | 复用作快照 |
| 插话协议 | `turn/steer`，`clientInputId` 幂等，安全点生效 | 复用 |

现有链路直接给手机用的四个障碍：

1. **接不上。** `/api/ws/browser` 校验 Origin 白名单，且只认 HttpOnly cookie。原生 App 没有这两样，不应伪造。
2. **权限太大。** 中继的操作表是 `desktopOperations`：`call` 能转发任意 RPC，还有 `setModelProviderKey`、`deleteModel`、`readClipboardText`、`readLocalArtifact`、`openExternal`。手机丢失不该等于这些能力全部丢失。
3. **断线即丢。** `broadcast_notification` 只发给在线 peer，没有序号也不缓存。手机经常掉线，不能依赖事件流维持状态。
4. **登录有 Turnstile。** 密码登录要过 Cloudflare Turnstile，原生里接它不划算。扫码配对正好绕开。

## 3. 架构

```text
Android App
  │  WSS  /api/ws/client?device=<id>
  │  Authorization: Bearer <手机 session>，客户端类型 mobile
  ▼
loom_web_gateway（只转发 + 操作名白名单）
  ▲
  │  WSS 外连，已有
Loom Host（Electron）── mobileOperations 表 ── App Server
                                                remoteControl/*
                                                  └ RemoteControlClient(channel="android")
```

改动点：

- **Account**：新增 `mobile` session 类型和 scope。session 单独列出、可单独吊销，不能换取模型凭据。手机 access token 不走 `relay-credential` 以外的账号 API。
- **Gateway**：新增 `/api/ws/client`，Bearer 认证，不校验 Origin，`kind=mobile` 的 peer 只能调用 `mobile.*` 操作。其余沿用 `BrowserPeer` 的 32 个并发上限、120 秒超时和 30 秒身份复查。
- **Host**：新增 `mobileOperations` 表，不共用 `desktopOperations`，不提供通用 `call`。这里才是权限的权威，gateway 白名单只是第二道。
- **App Server**：新增 `remoteControl/*` 方法，在进程内包 `RemoteControlClient`，策略和指纹只保留一份实现。

### 手机操作表

| 操作 | 说明 |
| --- | --- |
| `mobile.inbox` | 一次返回：需要你、运行中、刚结束，带 revision |
| `mobile.thread` | 手机投影：文字、折叠的工作记录、当前步骤、`pendingApproval` 与指纹 |
| `mobile.threadOlder` | 向前翻页 |
| `mobile.taskStart` | 新任务，强制 `approval` 权限模式 |
| `mobile.steer` | 带 `clientInputId` |
| `mobile.stop` | 不受权限模式限制（与现有 `task_stop` 一致） |
| `mobile.approve` | 指纹 + `accept`/`decline`，可带拒绝原因（`RemoteControlClient.approval_respond` 目前不收原因，需扩展） |
| `mobile.projectOverview` | 建造模式概览，第 4 阶段 |

`mobile.thread` 不直接透传 `thread/read`，由 Host 投影：工具调用折叠成一行摘要，输出只留末尾几行，大载荷替换为大小说明。`item/delta` 在 Host 侧合并到每秒约 4 次。

## 4. 安全模型

### 配对

1. 电脑上「远程设备 › 添加手机」，Host 申请票据并显示二维码：票据、`deviceId`、Host 公钥。
2. 手机扫码，在 Keystore 生成自己的密钥对，提交票据和公钥。
3. 两端用双方公钥算出同一个 6 位核对码并显示。
4. 用户在**电脑上**点允许，账号服务才发 `mobile` session。

未确认的扫码不产生任何权限，所以二维码被拍到也无法直接接管。

### 控制指令签名

现在 gateway 能看到并转发所有内容，它若被攻破就能伪造批准。手机用配对时的私钥对 `mobile.approve`、`mobile.taskStart`、`mobile.steer` 签名，内容含线程、指纹或文本哈希、时间戳和计数器；Host 用配对时记下的公钥验证。这样 gateway 只能转发，不能代批。

端到端加密内容留到第 4 阶段，v1 中继仍能看到明文，这一点要写进隐私说明。

### 其他规则

- 手机批准只有 `accept`/`decline`。协议里的 `acceptForSession`、`acceptWithPrefixRule` 不开放，长期放权只能在电脑上做。
- 批准前可要求指纹解锁，默认开启。
- 审批页设 `FLAG_SECURE`。token 存 Android Keystore 包裹的加密存储。
- 通知里不能直接批准，也不带命令内容：推送会经过第三方。
- 现有 `RemoteControlPolicy` 只允许控制 `read-only`、`approval` 模式的线程。手机新任务固定 `approval`。全权模式线程在手机上能看、能停，不能插话或批准（见开放决定 1）。
- 电脑端可一键吊销某部手机，吊销后 30 秒内断开（沿用身份复查周期）。

## 5. 连接与状态

**快照是真相，事件流只是加速。** 手机每次回前台、重连或收到通知，先取 `mobile.inbox` 和当前线程快照，再订阅事件。不做事件补发，也就不必给 gateway 加持久化。

- gateway 是单实例内存路由，自动部署时全部连接会断，客户端要把这视为常态：指数退避 + 重取快照，界面只显示「正在重连」。
- 写操作带幂等键。不确定是否送达时，用同一键重试，不让用户点第二次。
- 手机缓存只存最近一次 inbox 和线程摘要。离线时只读，并标明「离线前看到的」，审批入口不可用。
- 电脑休眠时 Host 掉线。是否在任务运行时阻止休眠作为电脑端设置，实现时确认现状。

## 6. 通知

这是最不确定的一块。国内安卓手机普遍没有可用的 FCM，厂商系统也会杀后台。

| 阶段 | 方案 | 代价 |
| --- | --- | --- |
| v1 | 用户电脑有任务运行时，前台服务保持 WSS 并显示常驻通知；Android 16 用进度型 Live Update | 无需第三方；App 被杀就收不到，需引导电池白名单 |
| v2 | gateway 接厂商推送或聚合服务，只发「有事件」信号，App 醒来再走 WSS 取内容 | 厂商通道通常要求企业主体和上架资质，实现前核实 |

前台服务的类型和时长限制（如 `dataSync` 在 Android 15+ 的 6 小时上限）实现前按当时的官方文档核对。

## 7. 客户端技术

选 **Kotlin + Jetpack Compose 原生**，放在仓库 `android/`。

不包 WebView 或 Capacitor：现有 renderer 是桌面形态，桌面端花了大量力气才把性能和动效压下来，而手机要的通知、前台服务、生物识别、Keystore、底部审批页都是原生能力。代价是不共享 renderer，但手机只渲染投影后的简化内容，工作量可控。

| 模块 | 选型 |
| --- | --- |
| 网络 | OkHttp WebSocket + kotlinx.serialization |
| 缓存 | Room |
| 密钥 | Android Keystore + BiometricPrompt |
| 扫码 | CameraX + 离线条码库（不依赖 Google Play 服务） |
| 版本 | minSdk 29，targetSdk 36 |

本机已有 Android SDK（platform 36、build-tools 36）和 JDK 17，可以本地构建。CI 需新增 Android job。

视觉沿用 `--loom-workspace-*` 深色令牌，紫色 `#b5a7f5` 表示运行，琥珀 `#e8b45a` 只表示「需要你」。动效遵循现有规则：文字只淡入不位移。界面文字中英双语。

## 8. 分期

| 阶段 | 内容 | 完成标准 |
| --- | --- | --- |
| 0 通道 | `mobile` session、`/api/ws/client`、`mobileOperations`、电脑端配对页 | 契约测试证明手机 token 调不了操作表外的任何操作 |
| 1 只读 | 配对、现在页、对话页、离线页 | 真机切网和杀进程后状态正确恢复 |
| 2 控制 | 批准、停止、插话、新任务、签名 | 伪造和重放的控制帧被 Host 拒绝；陈旧卡片批准失败 |
| 3 通知 | 前台服务、常驻通知，然后接推送 | 目标手机锁屏 1 小时后仍能收到审批提醒 |
| 4 扩展 | 建造模式、语音输入、附件、端到端加密 | 按需拆分 |

建议先做 0 到 2：这时已能手动打开 App 完成批准，也能验证手机到 `loom.smirel.com` 的真实延迟。

## 9. 开放决定

1. **全权模式线程能否从手机插话？** 建议默认不能，按电脑逐台在电脑端开启。
2. **发行方式。** 建议先在 GitHub Release 发 APK 自用；要上架或给他人用，需要先定推送和资质。
3. **主力测试手机的品牌和系统版本。** 决定后台保活策略，也决定是否有 FCM。
4. **v1 明文经过中继是否可接受。** 建议接受，与现有 Web 相同；签名已防代批。

## 10. 验证要点

- 权限测试以「打破」为准：拿手机 token 直接发 `call`、`setModelProviderKey`，直连 `/api/ws/browser`，必须全部失败。
- 中继被攻破的模拟：gateway 改写 `mobile.approve` 载荷，Host 必须拒绝。
- 网络：弱网、切 Wi-Fi/蜂窝、gateway 重启、Host 退出后重启。
- 这台电脑访问 smirel.com 走代理，手机直连的延迟不能拿电脑上的数字代替，要在手机上实测。
