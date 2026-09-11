# Browser Use diagnostics

Loom writes local Browser Use diagnostics for all browser automation backends:

- extension current-tab bridge;
- browser-use local launch;
- browser-use CDP attach.

The default log folder is:

```text
.loom/logs/browser-use
```

Each run creates an append-only JSONL file:

```text
browser-<timestamp>-<pid>.jsonl
```

## What gets recorded

The logs are intended for local smoke testing and bug reports. They include:

- backend mode: `extension-bridge`, `local-launch-persistent`, `local-launch-ephemeral`, or `cdp-attach`;
- bridge lifecycle, extension registration, command queue/dispatch/result timing;
- browser-use session create/start/stop strategy;
- browser-use event dispatch timing and failure summaries;
- action names, element index, tab id, state revision, tab count, selector count;
- URL/title, bounded DOM size/excerpt, error summaries;
- screenshot byte counts without screenshot bytes.

## Privacy boundaries

Diagnostics are local-only. They are still detailed, so treat exported archives as test artifacts.

The logger intentionally avoids high-risk payloads:

- screenshot bytes are omitted;
- CDP endpoints and browser profile paths are represented only by exposure booleans;
- secret-shaped keys are redacted;
- URL query and fragment values with token/session/password-like keys are redacted;
- `browser_type` records text length and presence, not typed text;
- post-`browser_type` state summaries omit DOM excerpts so newly typed text is not copied back through the refreshed page state.

## Enable or disable

Diagnostics are enabled by default. To disable them:

```bash
LOOM_BROWSER_DIAGNOSTICS=0
```

To choose a per-case folder:

```bash
LOOM_BROWSER_LOG_DIR=.loom/logs/browser-use/case-001
```

Both `npm run dev:browser` and `npm run dev:extension` print the diagnostics folder they pass into the Python App Server.

## Export

Open Settings → Browser and click **Export browser logs**. The desktop app packages the Browser Use log folder into a zip and reveals it in the native file manager.
