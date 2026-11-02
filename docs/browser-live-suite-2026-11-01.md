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
