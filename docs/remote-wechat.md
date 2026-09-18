# Loom WeChat Remote

`loom-remote-wechat` lets one paired WeChat user control Loom through WeChat Customer Service.

The first implementation is intentionally a **local polling transport**:

```text
WeChat
  -> WeChat Customer Service
  -> qyapi.weixin.qq.com / kf/sync_msg
  -> loom-remote-wechat on your computer
  -> Loom App Server
  -> Agent Runtime
```

This gives Loom a usable phone remote without opening a public port or configuring a callback URL first. The computer still needs to be online. A later cloud relay can reuse the same App Server bridge and pairing model.

## Security model

- Loom keeps the normal App Server / Agent Runtime permission boundary.
- Remote control starts in `approval` mode by default.
- The WeChat API secret and access token are never written to Loom's remote state file.
- On first launch Loom prints a one-time numeric pairing code.
- The first WeChat user that sends the matching `/bind <code>` becomes the only authorized remote user.
- Messages from every other `external_userid` are ignored.
- Dangerous tool approvals are correlated back to the original App Server approval request and require `/allow` or `/deny`.

Remote state is stored under:

```text
~/.loom/remote/wechat-customer-service.json
```

It contains only the opaque WeChat user id, customer-service account id, Loom thread id, sync cursor, and a bounded message-id dedupe window.

## WeChat / WeCom setup

You need a WeChat Customer Service account managed through the WeCom API.

In the WeCom admin console:

1. Enable **WeChat Customer Service**.
2. Enable API management for the customer-service account you want Loom to use.
3. Copy the enterprise `CorpID`.
4. Copy the **WeChat Customer Service Secret** shown after API is enabled.

`open_kfid` is optional when the enterprise has exactly one customer-service account; Loom discovers it with `/cgi-bin/kf/account/list`. If several accounts exist, pass the id explicitly.

The WeChat customer-service account itself is not a personal WeChat account and Loom does not hook or automate the Windows WeChat client.

## Configuration

Set the two required environment variables:

PowerShell:

```powershell
$env:LOOM_WECOM_CORP_ID="ww..."
$env:LOOM_WECOM_KF_SECRET="..."
```

If the enterprise has several customer-service accounts:

```powershell
$env:LOOM_WECOM_OPEN_KFID="wk..."
```

Loom's normal model configuration is still required, for example `LOOM_MODEL`, `LOOM_PROVIDER`, `LOOM_BASE_URL`, and the corresponding model API key.

## Start

From the Loom repository or an installed package:

```powershell
loom-remote-wechat --workspace C:\Users\you\Loom
```

At startup Loom prints:

- the selected `open_kfid`
- a WeChat Customer Service contact URL when the account permits generating one
- a pairing command similar to `/bind 731842`

Open the contact URL with WeChat and send the pairing command.

## Commands

After pairing:

```text
/status  show current Loom thread / task state
/new     create a new remote Loom thread
/stop    interrupt the active turn
/allow   approve the pending sensitive tool operation
/deny    reject the pending sensitive tool operation
/help    show command help
```

Any other text becomes a Loom task. If a turn is already running, the message is sent through the existing `turn/steer` protocol as a live steering instruction.

## Polling behavior

The bootstrap transport uses `kf/sync_msg` without the short-lived callback token. The API permits this but applies stricter frequency limits, so Loom defaults to a conservative 30-second poll interval and refuses intervals below 10 seconds.

This mode avoids public callback infrastructure. The planned cloud relay can later switch to signed callback events, pass the callback token into `sync_msg`, and keep accepting phone tasks even while the desktop node is offline.

## Current scope

The first implementation accepts customer-origin **text** messages. Images/files are deliberately deferred until Loom Remote has a media-download and attachment-staging boundary that preserves the same file size, path, and permission guarantees as the desktop client.

Replies are truncated to stay within WeChat Customer Service text-message limits rather than splitting one Agent answer into many outbound messages.

## Reset pairing

To bind a different WeChat identity:

```powershell
loom-remote-wechat --workspace C:\Users\you\Loom --reset-binding
```

A fresh pairing code is generated. Existing Loom conversation data is not deleted.
