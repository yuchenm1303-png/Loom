# Loom Weixin Remote（普通微信 + 腾讯 iLink）

`loom-remote-wechat` 现在使用 **普通微信扫码 + 腾讯 iLink Bot API** 作为 Loom 的本机 Remote Channel。

```text
普通微信
  -> 腾讯 iLink Bot API
  -> Loom Weixin Channel
  -> Loom Remote Service
  -> Loom App Server
  -> Agent Runtime
```

它不再依赖企业微信、微信客服、CorpID、Secret、营业执照或公网 callback。第一版本机进程必须保持在线。

## 安全边界

- 微信 Channel 只负责认证后的文本收发，不直接控制 `AgentRuntime`。
- 所有任务继续通过 Loom App Server 的 `thread/*`、`turn/*`、`approval/*` 协议。
- Loom 原有 approval / sandbox / permission 继续生效；默认新远程会话使用 `approval`。
- 扫码成功返回的 `ilink_user_id` 是唯一允许下达指令的微信身份。
- 首次绑定会记录时间边界；绑定前的历史 `getupdates` 消息不会变成 Loom 任务。
- 入站 `message_id` 在执行前持久化去重，配合先落 `get_updates_buf` 的策略实现 at-most-once ingress。
- `bot_token` 只进入操作系统 keyring，不写入 Loom JSON 状态，也不会提交 Git。
- `context_token` 只保存在当前进程内存中，不写日志或状态文件。
- Loom 的内部 sticker / protocol marker 在发往微信前会被剥离。

非敏感状态位于：

```text
~/.loom/remote/weixin.json
```

其中只保存 iLink bot/user ID、API base URL、Loom thread、`get_updates_buf`、历史边界和有限的消息去重窗口。

## 首次启动

安装当前分支后，在 Loom 仓库中运行：

```powershell
loom-remote-wechat --workspace C:\\path\\to\\Loom
```

第一次启动时终端会显示二维码。用**普通微信**扫描并在手机上确认授权。

扫码流程按腾讯当前 iLink 客户端协议处理：

1. `POST /ilink/bot/get_bot_qrcode?bot_type=3`
2. `GET /ilink/bot/get_qrcode_status` 长轮询
3. 处理 `wait`、`scaned`、`need_verifycode`、`scaned_but_redirect`、`expired`、`verify_code_blocked`、`binded_redirect`、`confirmed`
4. `confirmed` 后保存 `ilink_bot_id` / `ilink_user_id` / `baseurl`，并将 `bot_token` 写入 OS keyring

之后重启会从状态文件 + keyring 恢复登录，不需要每次扫码。

如果需要重新绑定：

```powershell
loom-remote-wechat --workspace C:\\path\\to\\Loom --reset-login
```

旧参数 `--reset-binding` 暂时作为同义别名保留。

## 收消息

Channel 使用：

```text
POST /ilink/bot/getupdates
```

每次请求带上上一次服务端返回的 `get_updates_buf`。新的 cursor 在本批消息执行前先持久化；如果进程恰好在执行期间崩溃，Loom 选择“可能要求用户重发”而不是“自动重放一个可能已经执行了一半的任务”。

第一版只接受：

- `message_type = USER`
- `item_list` 中的文本项
- `from_user_id == 扫码确认的 ilink_user_id`
- 有稳定 `message_id`
- 有可用于回复的 `context_token`

图片、语音、文件、视频等非文本消息会明确忽略，不会误交给 Agent。

## 发消息

回复使用：

```text
POST /ilink/bot/sendmessage
```

每个请求：

- 使用当前入站消息的 `context_token`
- 生成唯一 `client_id`
- 使用 Bot / FINISH 文本消息类型
- 长文本按 UTF-8 安全边界拆成多条请求

Loom 不把 `context_token` 当成永久凭证，也不将它持久化。当前第一版只保证在有最近有效入站上下文时回复；如果腾讯服务端未来收紧上下文生命周期，需要根据真实服务端响应再调整，而不是假设 TTL。

## Loom 命令

```text
/status  查看当前 Loom thread / task 状态
/new     新建远程 Loom 会话
/stop    turn/interrupt 当前任务
/allow   approval/respond 允许当前审批
/deny    approval/respond 拒绝当前审批
/help    查看帮助
```

普通文本行为：

- thread idle -> `turn/start`
- thread running -> `turn/steer`
- waiting_approval -> 要求先 `/allow` 或 `/deny`
- running / waiting_approval 时 `/new` 会被拒绝，避免丢失旧任务绑定

新任务启动后会立即回复：

```text
🟢 收到，Loom 已开始执行。
```

出现审批时立即推送审批信息；`turn/completed` 后再回最终结果。Channel 已预留 `send_progress()` 接口，第一版不做复杂 streaming。

## 断线与凭证失效

`getupdates` 是长轮询。客户端会：

- 使用服务端返回的 `longpolling_timeout_ms`
- 将客户端长轮询 timeout 当作正常空轮询继续
- 普通网络/API 失败短重试，连续失败后退避
- iLink 返回当前客户端已识别的失效登录信号（包括 `-14`）或 HTTP 401/403 时，停止当前 monitor 并重新进入 QR 登录

错误日志只记录错误类型和安全状态，不输出 `bot_token`、`context_token`、Bearer Authorization、二维码值或验证码。

## 当前范围

第一版目标只有一件事：**本机在线时，用普通微信文本安全遥控 Loom。**

暂不包含：

- 图片 / 文件 / 语音 / 视频输入
- 复杂 streaming / token 级进度推送
- 云端常驻 relay
- 多微信用户共享一个 Bot
- 产品化的 iLink-App-Id / 腾讯商业授权策略

这些以后都可以继续扩展在 `app/remote/channels/weixin/` 内，不需要绕过 Loom App Server 或复制 Agent 状态机。

## 代码结构

```text
app/remote/
    base.py
    service.py
    channels/
        weixin/
            api.py
            auth.py
            channel.py
            monitor.py
            state.py
```

`service.py` 是唯一保存 Loom thread / turn / approval 映射逻辑的地方。Weixin Channel 只做协议、身份、cursor、去重和安全文本收发。
