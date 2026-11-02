# Browser automation: live suite and what it changed (2026-11-01)

`scripts/browser_suite/` drives Loom's browser tools with a real model against a local test app
(`suite_site.py`, 127.0.0.1 only) and checks each task from what the page posted to the server, or for
the extraction task from the number in the final answer. It uses the isolated browser-use backend (a
separate browser with a temporary profile), never the user's Chrome, and does not touch the mouse or
keyboard. Nine tasks: sign-in then settings, a shop with a category filter and checkout, a three-step
wizard, an infinite feed (click Item 73), a key copied from a page that opens in a new tab, a payment form
in an iframe, totals read from an order table, a number input inside a shadow root, and a delete behind a
confirmation modal.

```
python scripts/browser_suite/run_suite.py OUT MODEL --stored-model ID --repeat 2 > X.log
python scripts/browser_suite/compare.py A.log B.log      # sums of per-task means
python scripts/browser_suite/digest.py OUT/state          # what each run actually did
```

## What changed

Every change came from reading what a run did.

- **`browser_batch`.** Up to 8 click/type/press/select/scroll steps on one state. Each index must be in
  the state the batch was planned on. Before every later step the target is found again by its
  descriptor (tag plus attributes or first text): the same index if it still matches, otherwise the one
  unique match. The batch stops at a vanished element or a failing step and reports what ran. Forms that
  took five to seven steps take two or three.
- **A change line per observation.** `text_changes_since_previous_observation` lists the text lines that
  appeared and disappeared since the previous state, ignoring index renumbering and rects. Nothing is
  said when nothing in the state changed. The state covers the page in view, and "no text changed" over
  a confirmation rendered below the fold sent both models debugging a click that had worked, for up to
  six minutes.
- **browser-use's own HUD is not page text.** Its "目标置信度 / 动作来源" overlay was serialized into the
  DOM state as a shadow block; it is removed in `_serialize_state`.
- **Index prefixes.** browser-use writes `|SHADOW(open)|[3]` and `*[3]` (new element), and the extension
  writes `[0] <button> text=".." rect=..`. The batch and change-line parsers read all three; the first
  live batch had failed on the shadow prefix.
- **`browser_look` keeps the page unless source changed this turn.** It reloaded by default, which threw
  away in-page state: the feed went back to the top and its click with it, three times in one run. Its
  description no longer offers `reload=true` as an option. MiniMax read "reload=true forces it" as a way
  to verify that a save persisted, found the field empty after the reload, and guessed API endpoints for
  five minutes.
- **record_check.** A URL or a Loom capture (`browser-screenshots/`, `browser-downloads/`,
  `browser-state/`) named as a subject no longer counts as development work, so a plain web task's visual
  check is recorded as the assistant's assessment instead of looping on the visual gate.

## Results

Sums of per-task means over two runs, deepseek-v4.1-flash and minimax-m3.1-flash.

The first page set had pages that hide their own state: the saved API key and the applied quantity did
not show again after a reload, and the feed's confirmation sat below the list. On it, DeepSeek went
from 535 s / 90 steps / 12 failed calls with 17/18 passing to 440 s / 62 / 4 with 18/18. A second run of
the same code took 518 s / 72 / 4. Much of the gap was the model working out whether an action had
worked.

The pages now show their state the way real pages usually do. The clicked feed button reads "Selected",
and a saved key or quantity is shown again on load. On those pages, comparing code from before this
pass (f9fa041a) with the final code, with each pair run at the same time so both arms see the same
provider load:

| | wall time | model steps | failed calls | passed |
| --- | --- | --- | --- | --- |
| deepseek, before | 567 s | 66 | 3 | 18/18 |
| deepseek, after | 595 s | 62 | 3 | 18/18 |
| minimax, before | 693 s | 82 | 3 | 18/18 |
| minimax, after | 701 s | 76 | 5 | 18/18 |

Steps fall 6–7%. Wall time is flat within noise:
- model time per step is 6.8–7.6 s in all four arms;
- tool time is about 0.9 s per call in both;
- spikes of 23–97 s per task in both arms are provider retries.

Batches were used 18 and 13 times. On pages that give clear feedback Loom's browser tools were already
near the floor. The cost left is the provider's time per step, and the wins of this pass are mostly
robustness: no HUD text, no state lost to a reload, no record_check loops, batches that parse every
backend's format.

## Open

- The extension backend, Loom's default for the user's own Chrome, could not be driven live from the
  harness:
  - Chrome 156 ignores `--load-extension`;
  - in Edge the extension loads and typing works, but CDP mouse events never reach the page.

  Its formats are covered by unit tests only.
- The extension bridge service outlives its runtime by design; the suite stops a leftover one on its own
  port (39333) at start and end.
- Run-to-run variance is large (one MiniMax response took 230 s). Compare means over repeats, and run
  before and after side by side.

## The user's own browser and the Loom app (2026-11-02)

`run_suite.py --backend live` joins the bridge the running Loom shares and drives the user's own browser
through the extension (Edge here); each task's tabs are closed afterwards. `run_app.py` sends the tasks as
conversations to the running desktop Host over local IPC, so they use the app's model, settings and browser
route, and archives them. Edge loads the unpacked extension from `%APPDATA%\Electron\browser\current-tab-extension`.
Writing a new token to that copy's `extension-update.json` makes the extension reload itself within about a
minute; `last_client_version` in the bridge status shows which version is running.

What the live path found, each fixed with a test:

- **Native clicks on a background tab stalled.** Loom opens its tabs in the background, and Chromium holds
  mouse input for a hidden widget. Every click waited 5.4 s and landed, if at all, the next time the tab was
  shown: a sign-in "did not submit", then had, two steps later. A click now fronts the tab only for the
  click and hands the user's tab back, as screenshots do. A click now takes 0.5 s. Typing (`insertText`)
  was never affected.
- **Element lines said too little.**
  - A checkbox was `<input> type="checkbox" value="on"`, ticked or not, and inputs had no label. Lines now
    carry `label=`, `checked=` and aria `expanded/selected/pressed` and `disabled`; batch relocation
    ignores those states.
  - Open shadow roots were not read, and repeated names ("Select" ×80) gave no hint of their row. Elements
    are now collected in composed order across shadow roots, and a repeated name gets `context=` from the
    nearest surrounding text.
  - Focus and hit tests go through shadow roots. The isolated backend had the same shadow focus bug in
    `browser_native_edit.py`.
- **Loom Browser was always offline in the app.** Its bridge and the desktop's local Host discovery both used
  127.0.0.1:39223, and Loom.exe held the port. Since local pages started defaulting to Loom Browser
  (bf770790), every localhost `browser_open` failed after 6 s, and the model spent two or three steps
  finding another browser. The bridge is on 39224 now, and when Loom Browser does not answer in 3 s the
  configured browser opens the page.

Results through the user's Edge, two runs per task, all passing:

| | wall time | model steps | failed calls |
| --- | --- | --- | --- |
| deepseek, first live run (extension 0.1.27) | 813 s | 76 | 1 |
| deepseek, extension 0.1.32 | 552 s | 68 | 0 |
| minimax, first live run | 822 s | 99 | 4 |
| minimax, extension 0.1.32 | 529 s | 80 | 2 |

For comparison, the isolated backend on the same code took 595 s / 62 steps (deepseek) and 701 s / 76 steps
(minimax). The widget task went from 99 s / 8.5 steps to 13 s / 3.

Nine tasks through the installed app (0.1.162, before the port fix) all passed, at 1194 s / 104 steps. Every
task started with the offline Loom Browser failure, and iframe took 28 steps.

Open:
- The extension does not read inside iframes. The payment-form task is done by coordinates plus typed text,
  at 11–16 steps against 5.5 on browser-use. Reading frames needs per-frame injection with each frame's
  offset, and real payment frames are cross-origin.
- CI is red from tests outside the browser, so the router does not release; these fixes reach the installed
  app with the next manually dispatched release.

### After 0.1.163 (port fix installed)

Nine tasks through the app went from 1194 s / 104 steps / 12 failed calls to **720 s / 61 / 2**, all passing.
Local pages now open in Loom Browser, and the iframe task took 4 steps, down from 28. Two more fixes came
out of this run (3869fa2c, 8940668d):
- **The first Loom Browser command after a start hung for 45 s.** It waited on the HUD script in a new tab
  that had no document yet. HUD scripts now give up after 1.5 s.
- **Endless lists did not load on scroll.** A background tab renders no frames, so the page never saw
  its scroll event. Read straight after the scroll, the state also still ended at the old last row. A scroll
  now fronts the tab, as a click does, and a scroll that reaches the bottom waits up to 1.2 s for the page to
  grow. In the user's Edge, five scrolls now load rows 21–101 at 0.9 s each; before, they stayed at row 20.

### Frames (extension 0.1.36–0.1.37)

The extension now reads every frame in one `executeScript({allFrames: true})`. The top frame lists its
visible iframes, each frame's elements are matched to one (by URL, then in order), and they are listed
under a `Frame N (url) at x,y` heading in page coordinates. Element refs carry their frame:
- **click** places the point by the iframe's offset;
- **type** clicks the field natively first, because a cross-origin frame takes focus from a gesture, not
  from script;
- **select and hover** run inside the frame.

A read waits up to 1.5 s for a visible frame that is still on its blank document. Without that wait, a
payment frame that arrived after the page was absent from the first state, and the model fell back to
coordinates for up to 28 steps. The suite's payment frame now arrives 0.8 s late, and there is a
cross-origin variant (`xframe`: a `localhost` frame on a `127.0.0.1` page).

In the user's Edge, three runs per task, all passing:

| | before | after |
| --- | --- | --- |
| deepseek iframe / xframe | 155 s, 16 steps / 97 s, 11 steps | 21 s, 4 steps / 30 s, 4 steps |
| minimax iframe / xframe | 134 s, 14 steps / 197 s, 14.5 steps | 46 s, 6.3 steps / 53 s, 6.7 steps |
