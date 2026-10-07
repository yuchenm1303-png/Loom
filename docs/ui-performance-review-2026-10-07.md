# Long-session desktop performance review

Baseline: `94eecb0991e5f8b03f75f172718b404a11633c56`, installed desktop 0.1.54.
The reported symptom is persistent UI lag, including new conversations after a
long-running conversation. The user's running Loom task was not restarted or
interrupted, and no real model requests were sent.

## Confirmed costs

1. **Hidden execution inspector retained the whole timeline.** App passed every
   transcript item to Inspector even when its sidebar was closed. A real App
   fixture with 20 turns and 2,000 process records retained roughly 44,000 CDP
   DOM nodes and 2,508 event listeners. Closed Inspector still built and updated
   every event row; its CSS visibility did not remove this work.
2. **Unrelated state updates revisited the current history.** App filtered the
   transcript and computed header counts on every render, passed new callbacks,
   and rendered both transcript and inspector on background thread updates.
   This work also ran while opening a new-conversation screen.
3. **Host IPC copied partial messages quadratically.** On each socket chunk the
   desktop main process concatenated the entire accumulated prefix again. An
   8,388,643-byte response arriving in 16 KiB fragments copied 2,160,066,595
   bytes in the previous implementation. That synchronous work can delay the
   whole desktop window, independently of the selected conversation.

The custom scrollbar MutationObserver initially looked suspicious, but it is
only imported by a test fixture and is absent from the installed production
bundle. It was not changed. The controlled conversation-switch test did not
show accumulating event listeners or documents; this review does not claim a
proven unbounded memory leak.

## Changes

- Inspector uses the existing panel presence lifecycle. Its items remain
  mounted during exit and exit reversal, then its event rows are released.
  The inspector shell retains its selected tab and expansion state.
- Memoize history-derived inputs and stable handlers at App's ownership
  boundary; memoize Transcript and Inspector so background status notifications
  can update the sidebar without rebuilding the active history. Actual
  transcript changes still flow through immediately.
- HostFrameDecoder retains fragments and assembles each complete message once,
  releasing partial fragments on disconnect. Existing framing, serialization,
  authentication and the 64 MiB message limit are preserved.
- Add a real-browser regression using the production App and styles to CI.
  It checks hidden rows, background notifications, panel exit reversal,
  repeated conversation switching and the new-conversation screen. A separate
  decoder regression measures copied bytes rather than machine-dependent time.

## Evidence

With the same long-history fixture, closed-panel CDP nodes fell from about
44,256 to 2,284; visible DOM elements are 1,818. Forty background thread updates
perform zero reads of current history item types while the sidebar receives
the updated running status.

After 12 long-history/small-conversation switches, CDP counters for the same
small conversation were: nodes 1,131 -> 1,134, event listeners 336 -> 336,
documents 1 -> 1. There remains one Host notification subscription. Opening a
new conversation removes all old turn blocks and inspector rows; subsequent
background updates do not revisit the old history.

Restoring the baseline App, Transcript and Inspector makes the new browser
regression fail: a closed panel creates 2,000 event rows instead of zero.
Restoring the fixes makes it pass. No production process was used for this
red/green check.

The 8 MiB IPC benchmark fell from 440 ms to 6 ms in a local sample and copies
only 8,388,639 body bytes. These are synthetic decoder timings, not an estimate
of the user's overall UI latency. Tests cover fragmented headers, Unicode,
binary payloads, multiple frames, invalid frames and incomplete-frame reset.

Validation includes Electron tests, desktop script tests, renderer build and
typechecking, full Python tests, and browser regressions for performance,
sidebar navigation, task-panel layout, task progress, streaming and workspace
visual behavior. CI runs the new performance browser test after the production
build.

## Existing test and style corrections

- Two panel source contracts previously required retaining hidden inspector
  data forever. They now require lifecycle-aware retention through the exit;
  the real-browser test additionally verifies reversal and eventual teardown.
- The folded-history source contract still named `progress.current` after the
  existing animated handoff changed it to `handoff.current`. Its heavy-subtree
  assertions are retained and its variable assertion follows the current code.
- Remove an external `.tg { display: none }` override that violated the existing
  thinking-glyph ownership contract. The glyph component owns its own display.
- The context-report test's single scripted response could be consumed by an
  asynchronous auto-title request. Give that test thread an explicit title so
  it tests the turn's context accounting deterministically. Assertions and
  production context code are unchanged.

## Deployment and remaining verification

These fixes change both renderer code and Electron transport. An updated
Python Host runtime alone cannot update the installed desktop UI or transport;
use a full desktop installer. Web renderer changes take effect after the web
bundle is deployed and refreshed. Do not restart an active user task to apply
the desktop update.

No isolated fixture can exclude all GPU/driver, account, real-output-size or
very-long-session costs. After installing the new desktop build, verify the
same workload, sidebar open/closed, and new conversations. If lag persists,
capture the affected desktop renderer/main process trace to locate the next
cost rather than infer it from elapsed time or add throttling heuristics.
