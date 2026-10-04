# Browser acceptance investigation — 2026-10-04

## Evidence and scope

Read the acceptance report, results.jsonl and harness under
`C:/Users/邹羽宸/Documents/Loom Workspace/browser_acceptance_2026-10-01`.
That directory is unchanged. Real action logs are under the user-level
`C:/Users/邹羽宸/.loom/logs/browser-use`, not only the repository log directory.

The report is partial acceptance, not a complete matrix: the stress run did not
finish, CDP attachment was unconfigured, and several capabilities were N/A.

## Findings

1. **Actual editing defects:** extension `typeText` assigned `.value` directly,
   bypassing readonly/maxlength and always appending for `clear=false`.
   The isolated backend delegated to browser-use's TypeTextEvent watchdog, whose
   exception path could fall back to typing into the currently focused page.
   Both now prepare the specific node and use Chromium `Input.insertText`.
   Browser-native editing owns maxlength, selection and beforeinput/input.
   Invalid/noneditable targets fail before input; no focused-field fallback.
2. **False success fallback:** extension coordinate clicks and keyboard actions
   previously fell back to synthetic page events if CDP failed. Synthetic Tab
   cannot perform the browser's default focus traversal. These paths now return
   an explicit native-input error rather than substituting a different action.
3. **Bridge liveness was mislabeled:** `browser-20261003_111038-10584` contains
   963 registrations and 16 queued timeouts, with no command dispatches. A
   registration updated `_last_poll_at`, making a heartbeat-only client appear
   to be a command reader. Registration and polling timestamps are now separate.
   Bridge fetches and server replies prohibit caching, and fetch response-body
   reading is bounded so a stalled request can enter the existing reconnect loop.
   These fix concrete protocol weaknesses; the historic browser-side reason for
   not polling cannot be uniquely recovered from server logs alone.
4. **Report assertions need correction:** `Input.insertText` is text insertion,
   not physical typing or an IME composition session. Requiring keydown/keyup or
   compositionstart/end for this tool is incorrect; use `browser_send_text` or
   `browser_press` when keyboard events are the intended behavior. A normalized
   DOM summary is also not exact textarea readback (whitespace is collapsed).
   Verify `JSON.stringify(element.value)`/a direct value read, not display prose.
5. **Version discrepancy:** the A-16 action in
   `browser-20261002_042123-26968` (seq 270–276) actually returned extension
   **0.1.18**, while the report says 0.1.19. Its coordinates (207,578) do fall
   inside the preceding C rectangle (176,547,61×61), so this is not demonstrated
   model mis-aim. That trace does not record whether native or synthetic delivery
   ran. Current native coordinate input reproduces successfully in real Edge.
   Escape/Tab on A were inferred from isolated tests, not independently executed
   on A; they cannot be counted as confirmed failures of both implementations.

## Verification

`tests/browser_native_acceptance.cjs` launches disposable headless Edge with a
separate background target tab. It executes the extension's real action
functions through a CDP adapter and the isolated backend's exact preparation
function. Checks cover CJK/emoji, trusted input events, maxlength, readonly,
disabled, textarea newlines, caret/selection replacement, empty clearing,
contenteditable selection, Escape keydown, Tab focus traversal, and trusted
coordinate click. It does not claim to validate the installed extension's live
network polling, user profiles, CDP attachment setup, or the full stress matrix.

Python regression tests additionally verify node-frame/session binding, remote
object cleanup, exact text transport, and registration-vs-reader diagnostics.

Reload/install extension **0.1.20** before live acceptance. Retest bridge command
collection and the previously inconclusive stress/CDP cases; do not label the
entire acceptance matrix passed based only on these focused regressions.
