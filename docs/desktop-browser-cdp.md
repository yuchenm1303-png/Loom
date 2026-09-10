# Desktop Browser CDP bootstrap

Loom Browser Use can run in two modes:

1. local `browser-use` launch mode, where Loom owns the Chromium session; and
2. local CDP attach mode, where Loom connects to a user-visible Chrome or Edge instance through `LOOM_BROWSER_CDP_URL`.

For desktop development, prefer the CDP helper so Electron, the Python App Server, and the BrowserRuntime inherit one explicit local endpoint.

## Setup

Install the Python desktop/browser dependencies:

```bash
npm run setup:python
```

Root-level Python development installs also include the browser extra:

```bash
pip install -r requirements.txt
```

## Start Loom with a visible browser

From `desktop-react/`:

```bash
npm run dev:browser
```

For a production-style local start:

```bash
npm run start:browser
```

The helper searches for Microsoft Edge first, then Google Chrome. It launches a separate browser profile under `.loom/browser/cdp-profile`, binds CDP only to `127.0.0.1`, sets `LOOM_BROWSER_CDP_URL=http://127.0.0.1:9222`, and then starts the requested npm script with that environment.

To only launch the browser and print the endpoint:

```bash
npm run browser:cdp
```

To check the command without launching a browser:

```bash
npm run browser:cdp:dry-run
```

## Configuration

The helper accepts these environment variables:

| Variable | Purpose |
| --- | --- |
| `LOOM_BROWSER_EXECUTABLE` | Absolute Edge/Chrome executable path override. |
| `LOOM_DESKTOP_BROWSER` | Preferred engine: `edge`, `chrome`, or `system`. Defaults to `edge`. |
| `LOOM_BROWSER_CDP_PORT` | Local CDP port. Defaults to `9222`. |
| `LOOM_BROWSER_CDP_PROFILE_DIR` | Dedicated browser profile directory. Defaults to `.loom/browser/cdp-profile`. |
| `LOOM_BROWSER_CDP_URL` | Existing local CDP endpoint to reuse instead of launching a browser. |
| `LOOM_DESKTOP_BROWSER_CDP=0` | Disable the helper. |

On Windows, a manual Edge launch equivalent is:

```powershell
msedge.exe --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir="$PWD\.loom\browser\cdp-profile" --new-window about:blank
$env:LOOM_BROWSER_CDP_URL = "http://127.0.0.1:9222"
```

## Runtime behavior

The Python BrowserRuntime still validates the CDP URL itself. It only accepts explicit loopback hosts, refuses credentials/query/fragment in the CDP endpoint, hides the endpoint from model-visible status, creates an `about:blank` work tab on attach, and disconnects without killing the user-owned browser.

Use `browser_status` inside Loom to confirm the result. In the attached mode the relevant fields should look like:

```json
{
  "enabled": true,
  "backend": "browser-use",
  "browser_connection": "cdp-attach",
  "external_browser": true,
  "cdp_endpoint_exposed": false,
  "session_persistence": "external-browser"
}
```

## Smoke check

After starting with `npm run dev:browser`, ask Loom:

```text
Use browser_status, then open https://example.com and take a browser screenshot.
```

A healthy run should expose `browser_open`, attach to the local browser, navigate the work tab, and save a PNG under the active workspace's `browser-screenshots/` folder.
