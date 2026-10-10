# Motion lab

Tools for looking at Loom's live motion with numbers and pictures instead of by feel. They run the real
renderer (the fixtures in `../fixtures`) in headless Chrome. None of them is a test; the tests that guard
what they found are the `*-browser.mjs` files next to them.

Environment, as for the browser tests (see `../../README.md`): `LOOM_PLAYWRIGHT_MODULE`, `LOOM_CHROMIUM_PATH`,
and `LOOM_TEST_ORIGIN` for a Vite dev server serving `desktop-react` (the `motion-fixture` entry in
`.claude/launch.json`, port 5199).

| Script | What it answers |
| --- | --- |
| `trace.mjs <url> <out.json> [ms]` | Records, every animation frame, the rectangle, height and opacity of every step, stage, message, capsule and fold of a live run (`live-app.html?scenario=code|approval|error|decision|quick`), plus scroll position and the typed length of the receiving message. |
| `analyze.mjs <trace.json> [px]` | Lists every discrete jump in that trace: an element that moves, changes height or fades by more than `px` in one frame, scroll jumps, frames over 40 ms. A smooth motion has none; a "pop" shows up as one line. |
| `timeline.mjs <trace.json> <regex> [from] [to]` | How the elements whose label matches move over time, printed only when something changes. |
| `cadence.mjs <trace.json>` | The typing rhythm of the receiving message: graphemes per reveal, gaps between reveals, graphemes per second. |
| `probe.mjs [theme] [case,...]` | One transition at a time (a step is appended, a step ends, a command prints, a step fails, a stage is superseded, older steps fold, the turn completes) sampled every frame as height / opacity / top of the elements that matter. |
| `conflicts.mjs <url> [ms]` | Stacked motion in a live run: nested elements that fade at the same moment (their opacities multiply) and elements with two animations or transitions on one property. |
| `states.mjs <outDir> [theme] [dsf] [scene,...]` | Static pictures of the work log in its states (mixed, failed, long, stages, open drawers, hover, a real session) at any device scale, for reading type and alignment. |
| `film.mjs <url> <outDir> [intervalMs] [durationMs]` and `sheet.mjs <dir> <out.png> <frames> <x,y,w,h>` | Screenshots of a run every `intervalMs`, and a contact sheet of the ones you pick. |

What they found, and what was done about it, is written up in
`docs/runtime-motion-thread-2026-10-10.md`.
