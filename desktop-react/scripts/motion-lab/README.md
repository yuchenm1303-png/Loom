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

## Icons

The icon lab (`../fixtures/icon-lab.html`, served by the same Vite entry) renders every redrawn glyph in the real
stylesheet cascade at rest, engaged, pressed and expanded; `?only=trash,copy&size=28&theme=light` narrows it.
These tools work on the lab and on the live app fixture. What they found, and how the icon layer works, is written
up in `docs/icon-motion-2026-10-10.md`.

| Script | What it answers |
| --- | --- |
| `icon-sheet.mjs [--only a,b] [--size 28] [--dsf 2] [--theme dark]` | A contact sheet of the lab: every glyph, every state, settled. For reading shapes and end poses. |
| `icon-film.mjs --icon trash,plus [--times 0,50,100,…] [--zoom 1]` | One glyph's gesture frame by frame, in and out. Hovers the lab's live cell, pauses every transition under it and seeks, so frames are exact instead of timed. `--size 16 --dsf 1.5 --zoom 3` shows what the user's display really draws. |
| `icon-film-live.mjs --target "<selector>[##<selector>]"` | The same for real controls in the app (colours, clipping and the control's own wash included). `--reveal "<row>"` hovers a row first, for actions that only appear on row hover. |
| `icon-audit.mjs --scene settings\|model\|menu\|…` | Hovers every icon control in a scene and prints how far each part moved, plus what still interferes: a control that lifts, a glyph with its own transform or filter. |
| `icon-rules.mjs --click … --target … --hover …` | Which stylesheet rules (file and line) still set a motion property on one element, with `:hover` forced through the DevTools protocol. The way to find the old rule that moves something twice. |
| `icon-spring.mjs [zeta] [settle]` | Prints a CSS `linear()` easing for a damped spring; `--ic-spring` in `icon-motion.css` came from it. |

Tests: `scripts/tests/icon-motion.test.mjs` (static: names, parts, ownership, dead lucide selectors) and
`scripts/tests/icon-motion-browser.mjs` (real hover, focus, reduced motion, rows and tools, loaders, press).
