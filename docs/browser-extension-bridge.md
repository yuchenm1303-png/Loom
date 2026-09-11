# Browser extension current-tab bridge

This mode lets Loom inspect and control the user's currently active Chrome/Edge tab. It is different from the `browser-use` local-launch/CDP helper:

- `browser-use` owns a Loom browser session.
- CDP attach controls a visible browser that was launched with a debugging port.
- The extension bridge controls the current tab in the browser where the Loom extension is installed.

Use this when the user asks Loom to "look at my current browser" or "operate this tab".

## Install the unpacked extension

1. Open Chrome or Edge.
2. Go to the browser's extensions page.
3. Enable Developer mode.
4. Choose "Load unpacked".
5. Select this folder from the repository:

```text
extensions/browser-current-tab
```

The extension defaults to:

```text
Bridge URL: http://127.0.0.1:39222
Token: loom-dev-browser-extension
```

You can edit those values from the extension's options page.

## Start Loom in current-tab mode

From `desktop-react/`:

```bash
npm run dev:extension
```

For a production-style local start:

```bash
npm run start:extension
```

To only print the bridge settings without starting Loom:

```bash
npm run browser:extension
```

The helper sets:

```text
LOOM_BROWSER_BACKEND=extension
LOOM_BROWSER_EXTENSION=1
LOOM_BROWSER_EXTENSION_PORT=39222
LOOM_BROWSER_EXTENSION_TOKEN=loom-dev-browser-extension
```

## Environment overrides

| Variable | Purpose |
| --- | --- |
| `LOOM_BROWSER_BACKEND=extension` | Select the browser extension backend. |
| `LOOM_BROWSER_EXTENSION=1` | Also enables the extension backend. |
| `LOOM_BROWSER_EXTENSION_HOST` | Bridge host. Must be `127.0.0.1` or `::1`. |
| `LOOM_BROWSER_EXTENSION_PORT` | Bridge port. Defaults to `39222`. |
| `LOOM_BROWSER_EXTENSION_TOKEN` | Shared token that must match the extension options. |
| `LOOM_BROWSER_EXTENSION_TIMEOUT` | Seconds Loom waits for an extension command result. Defaults to `45`. |

## Runtime behavior

When this mode is active, `browser_status` should report:

```json
{
  "enabled": true,
  "backend": "browser-extension",
  "browser_connection": "extension-bridge",
  "external_browser": true,
  "storage_state_persistence": true,
  "extension_bridge": {
    "connected": true,
    "token_exposed": false,
    "url_exposed": false
  }
}
```

The bridge runs only on loopback. The shared token is not exposed to model-visible status or tool descriptions.

## Supported MVP actions

The current extension backend supports:

- read active tab URL/title/text/interactive elements;
- navigate current tab or a new tab;
- click by Loom element index;
- type into input/textarea/contenteditable elements;
- scroll;
- refresh;
- go back;
- list/switch/close tabs in the current browser window;
- visible-tab screenshot;
- hover, key press, select, and basic drag/drop.

Element indexes are still protected by Loom's existing `state_revision` check. After the page changes, ask for `browser_state` again before clicking or typing.

## Limitations

This is a current-tab DOM bridge, not a full desktop GUI driver. It cannot inspect or control `chrome://`, `edge://`, extension pages, operating-system dialogs, file pickers, or browser toolbar UI. Use Computer Use as a fallback for those surfaces.

It also cannot bypass login, CAPTCHA, MFA, or site security flows. If a site requires user approval, the user should complete that step in the browser and then ask Loom to continue.
