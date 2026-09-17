# Browser extension current-tab bridge

This bridge lets Loom inspect and control the user's currently active Chrome/Edge tab. It is different from the `browser-use` isolated-browser/CDP helper:

- `browser-use` owns a Loom browser session.
- CDP attach controls a visible browser that was launched with a debugging port.
- The extension bridge controls the current tab in the browser where the Loom extension is installed.

The desktop default is now **Automatic · current browser first**. Loom keeps the local extension bridge listening and resolves the route when `browser_open` starts a session:

1. if the extension is connected, Loom uses the current Chrome/Edge tab and its existing profile, cookies, login state, tabs, and extensions;
2. if the extension is not connected, Loom opens its own **visible isolated browser** and reports that fallback explicitly;
3. if the user selects **Current browser only**, a missing extension is an error and Loom does not silently open another browser.

CDP attach remains an explicit developer/debug route rather than the normal way to operate an everyday browser.

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

The normal desktop App Server starts the bridge automatically in `auto` or `extension` mode. The development helpers below remain useful when working on the extension itself.

## Start Loom in current-tab development mode

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
LOOM_BROWSER_LOG_DIR=<repo>/.loom/logs/browser-use
LOOM_BROWSER_DIAGNOSTICS=1
```

## Environment overrides

| Variable | Purpose |
| --- | --- |
| `LOOM_BROWSER_BACKEND=extension` | Select the browser extension backend for development/legacy startup paths. |
| `LOOM_BROWSER_EXTENSION=1` | Also enables the extension backend. |
| `LOOM_BROWSER_EXTENSION_HOST` | Bridge host. Must be `127.0.0.1` or `::1`. |
| `LOOM_BROWSER_EXTENSION_PORT` | Bridge port. Defaults to `39222`. |
| `LOOM_BROWSER_EXTENSION_TOKEN` | Shared token that must match the extension options. |
| `LOOM_BROWSER_EXTENSION_TIMEOUT` | Seconds Loom waits for an extension command result. Defaults to `45`. |
| `LOOM_BROWSER_LOG_DIR` | Folder for local Browser Use diagnostic JSONL logs. Defaults to `.loom/logs/browser-use`. |
| `LOOM_BROWSER_DIAGNOSTICS=0` | Disable Browser Use diagnostic logging. Enabled by default for extension mode. |

## Runtime behavior

When Automatic has a connected extension, `browser_status` reports the selected external route while keeping the requested policy explicit:

```json
{
  "enabled": true,
  "backend": "browser-extension",
  "requested_browser_connection": "auto",
  "auto_selected_connection": "extension",
  "browser_connection": "extension-bridge",
  "external_browser": true,
  "auto_fallback": false,
  "storage_state_persistence": true,
  "extension_bridge": {
    "connected": true,
    "token_exposed": false,
    "url_exposed": false
  }
}
```

When the bridge is offline in Automatic mode, status instead says `auto_selected_connection: "local-launch"`, `auto_fallback: true`, and includes a human-readable `auto_fallback_reason`. The isolated fallback is visible (`headless: false`).

The bridge runs only on loopback. The shared token is not exposed to model-visible status or tool descriptions.

## Browser diagnostics and one-click export

The extension bridge writes local JSONL diagnostics to:

```text
.loom/logs/browser-use/browser-<timestamp>-<pid>.jsonl
```

Each event records the bridge lifecycle, extension registration, command queue/dispatch/result timings, tab binding, action name, element index, state revision, URL/title, tab count, DOM size/excerpt, page HUD state, and extension errors. Secret-shaped fields are redacted, screenshot bytes are omitted, and `browser_type` stores text length instead of the typed text payload. DOM excerpts are also omitted from post-`browser_type` diagnostic summaries so newly typed text is not copied into the log through the refreshed page state.

To export logs from the desktop UI, open Settings → Browser and click **Export browser logs**. Loom will create a zip archive and reveal it in the native file manager. The same helper respects `LOOM_BROWSER_LOG_DIR`, so custom test runs can keep per-case logs in separate folders.

## Page-local browser HUD

The extension renders browser automation feedback inside the web page itself instead of using the desktop full-screen Computer Use overlay.

For read-only state collection, the page shows a compact top-right pill such as "Reading current tab". For element actions, the page draws a small target frame directly around the DOM element that Loom is about to hover, click, type into, select, or drag from. The HUD uses `pointer-events: none`, does not dim the whole page, and automatically disappears after the action.

Browser DOM events are no longer projected into Loom's full-screen desktop HUD. In particular, Loom does not manufacture desktop coordinates from an element index. The desktop HUD is reserved for Computer Use, where there is a real screen-space pointer target.

The page-local HUD is best-effort and only appears on injectable `http` and `https` pages. It is skipped for privileged browser surfaces such as `chrome://`, `edge://`, extension pages, and file picker/native OS dialogs.

## Tab ownership

Loom marks the tabs it works in and keeps that list in `chrome.storage.session`, scoped to the browser session rather than to a Loom session.

A navigate to a URL that is already open in the window reuses that tab instead of opening a duplicate. Reusing it makes it a Loom work tab, which means a later navigate to a different URL replaces what is on it, so the adopted tab is also placed into the purple **Loom** tab group: the group is the user's only visible record of which tabs Loom can steer. Tabs Loom opens itself are grouped the same way.

Closing the browser session hands ownership back (`release_tabs`). The tabs and the group stay exactly where they are; Loom simply stops claiming them, so the next task will not navigate away a page the user has gone back to using. The group id is kept so a later session reuses that group rather than creating a second one.

`browser_downloads` reports only downloads that started after the browser session opened. The extension can see the user's whole download history, and their filenames alone are revealing, so anything older is withheld.

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

Element indexes are still protected by Loom's existing `state_revision` check. After the page changes, ask for `browser_state` again before clicking or typing. The index-to-element mapping lives in `chrome.storage.session` so that it survives the MV3 service worker being evicted between commands.

## Limitations

This is a current-tab DOM bridge, not a full desktop GUI driver. It cannot inspect or control `chrome://`, `edge://`, extension pages, operating-system dialogs, file pickers, or browser toolbar UI. Use Computer Use as a fallback for those surfaces.

It also cannot bypass login, CAPTCHA, MFA, or site security flows. If a site requires user approval, the user should complete that step in the browser and then ask Loom to continue.
