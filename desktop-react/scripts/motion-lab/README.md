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
| `follow.mjs <url> [out.json] [width] [height] [dsf]` | Does the transcript stay on the live edge while a run streams? For every painted frame, how far the newest content lies below the viewport and whether follow is on; lists each episode where the view lagged, and every flip of follow. Use a short viewport so the run overflows. |
| `states.mjs <outDir> [theme] [dsf] [scene,...]` | Static pictures of the work log in its states (mixed, failed, long, stages, open drawers, hover, a real session) at any device scale, for reading type and alignment. |
| `film.mjs <url> <outDir> [intervalMs] [durationMs]` and `sheet.mjs <dir> <out.png> <frames> <x,y,w,h>` | Screenshots of a run every `intervalMs`, and a contact sheet of the ones you pick. |
| `jitter.mjs <url> [out.json] [width] [height] [dsf] [--cdp]` | Whether the live edge holds still: per painted frame the scroll step, reversals, sub-pixel steps, where landmarks sit, how far the typed line travels while the view is pinned and whether it goes back. |
| `animations.mjs <url> [out.json] [width] [height] [dsf] [--at 4000,9000]` | `document.getAnimations()` at chosen moments of a run: every running animation, its target and keyframe properties; anything that is not transform/opacity runs on the main thread every frame. |
| `style-cost.mjs <url> [width] [height] [dsf] [--from ms --to ms] [--off selectors]` | Style recalcs and layouts in a window of a run: counts, ms per second, elements per recalc, the invalidation reasons. `--off` switches animations off for a selector list first, which turns "how much is the ambient glow?" into one command. |
| `profile.mjs <url> [width] [height] [dsf] [--top 30]` | Where main-thread JavaScript goes during a run (the CDP profiler: trust ratios, not milliseconds). |
| `screencast.mjs <url> <outDir> [width] [height] [dsf] [--send] [--after ms] [--for ms] [--every n] [--mark class]` and `contact.mjs <frameDir> <out.png> <fromMs> <toMs> [cols] [crop] [scale] [max]` | Every frame the compositor actually produced, with its timestamp and the longest gap between two (a frozen screen shows up here even when the page's own timers look healthy), and a contact sheet of a stretch of them. |
| `tour.mjs <url> [outDir] [--shots] [--trace] [--popin] [--only a,b] [--theme] [--dsf]` | The whole interface driven the way people use it (sidebar, header toggles, inspector, settings and its pages, every composer popover, message actions, process disclosure, context menu). Per step: the worst frame, frames over 25ms, unrequested movement. `--trace` splits long tasks into script/style/layout/paint and names what forced the layout; `--popin` lists what appeared or vanished with no animation. |
| `popin.mjs <url> [scenario,...] [--theme]` (and `popin-probe.mjs`) | The same pop-in probe over the live-app scenarios. A node put where another just left (a wrapper, a remount under another parent) is not counted: only what really appears or goes with nothing carrying it. |
| `state-audit.mjs <url> [--scene home\|settings\|composer\|menu] [--states hover,focus,active] [--theme]` | Controls that change look on hover, focus or press without a transition (forces the pseudo-state through the DevTools protocol and reads computed style before and after). Focus rings appearing at once are right; borders derived from `currentColor` are false positives. |
| `fuzz.mjs <url> [from] [to] [--speed 4] [--parallel 3] [--theme dark\|light]` | Seeded random runs of the live-app `fuzz` scenario (stages, notes, approvals, parallel calls, failures; endings: answer, error, stop, a decision card), each checked: no page error, nothing left live or running, the process folded, the view on the live edge and only ever moving toward it while following, no infinite animation left, the typing glow out, no DOM growth. A failing seed replays alone (`from = to`, or `?scenario=fuzz&seed=N`). |

What they found, and what was done about it, is written up in
`docs/runtime-motion-thread-2026-10-10.md` (the work log) and `docs/runtime-motion-pass-2026-10-11.md` (the whole
pass: ornament cost, follow spring, streaming text, completion, the decision card, fonts).

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
| `send-film.mjs [--scene swap,gesture,guidance] [--theme dark] [--zoom 4]` | The send button on the real composer (live-app fixture), frame by frame at device pixels: `swap` is the handoff when a message is sent and when a run is stopped (the outgoing composer is a copy React drops after 280 ms, so the tool holds that timer while it takes frames); `gesture` is wake / pointer in / out / press / text deleted; `guidance` is the button that stays while a run is going. Written up in `docs/send-button-2026-10-12.md`. |

Tests: `scripts/tests/icon-motion.test.mjs` (static: names, parts, ownership, dead lucide selectors) and
`scripts/tests/icon-motion-browser.mjs` (real hover, focus, reduced motion, rows and tools, loaders, press).
