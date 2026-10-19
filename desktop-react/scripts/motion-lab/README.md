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
| `spec.mjs <origin> [--theme dark|light] [--only step,step] [--json out.json] [--all]` | What really runs when you click: the live-app fixture is driven through the most common interactions (popovers and menus, folds, thread and page switches, settings, profile, the docks) and, for each step, every animation and transition that started is listed with its effective timing (element, property or keyframes, duration, delay, curve, what it moves). Icon gestures, colour changes and ambient loops are left out unless `--all`; the last block counts the distinct duration+curve pairs of the whole tour (a dozen on the scale, fifty-odd before it). `spec-lib.mjs` is the recorder and the scenario list; `tests/motion-language-browser.mjs` asserts on the same rows. |
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
| `quill-film.mjs [--scene in\|out\|press\|click\|shortcut] [--probe 700] [--fixed 1] [--reduced 1]` | The quill of the sidebar's new-conversation row on the real sidebar (live-app fixture), frame by frame at device pixels: the pose the pointer gives it, the press toward its nib, and the line written again when a conversation is started (`click`, or `shortcut` for Ctrl+N, sent to the window as the event the app listens for: headless Chrome never delivers the real key, it is reserved). `--probe` prints the turn of the quill and barb and the length of the line every 20 ms. Written up in `docs/quill-2026-10-14.md`. |

## The quick-tool dock

The capsule at the window's right edge (`src/components/RightToolDock.tsx`, `right-tool-dock.css`); written up in `docs/right-tool-dock-2026-10-19.md`. Both tools run the live-app fixture; the behaviour is guarded by `scripts/tests/right-tool-dock-motion-browser.mjs` (mutation-checked 16 ways in a mirror).

| Script | What it answers |
| --- | --- |
| `dock-trace.mjs [from] [to] [--theme dark] [--dwell 700] [--ms 700]` | The glider and the name, one row per animation frame, while the pointer rests on one tool, travels to another and leaves: where the glider is and how it settles (a spring with half a percent of overshoot), when the name appears (after its 360 ms wait) and how it follows (its middle against the glider's), how both fade. The first appearance must not slide (the glider is parked at its place before it fades in). |
| `dock-film.mjs [--scene arrive\|carry] [--rate .1] [--every 350] [--frames 14] [--slots 3] [--tool review]` | A contact sheet. `arrive` reloads with animations at a tenth of their speed (CDP `Animation.setPlaybackRate`) and photographs the capsule sliding in and the tools following one after another; `carry` picks a tool up, carries it down `--slots` places (the others make room by the real distance, the rule included) and puts it down. |

## Sending

A message that is sent leaves the field as a capsule and lands in the conversation (`src/sendFlight.ts` makes it, `src/sendFlightMotion.ts` is its motion: Material's arc on one clock); the tools run the live-app fixture, whose Host answers a send after `?latency=` ms as a real one does (account check, request preparation).

| Script | What it answers |
| --- | --- |
| `flight-trace.mjs [--empty 1] [--latency 1200] [--theme dark] [--text "..."] [--height 860] [--css "<rules>"] [--png out.png]` | The flight in real time, one row per animation frame: the launch (with the time it takes to touch down, `arrive`) and land events (and a `correct` if the landing had to be adjusted, or a `skip` with its reason), the path of the message's first glyph and its peak speed, the real entry's place and the thinking row's arrival, the hand-off (last ghost frame against the first frame of the real bubble, in px), the worst frame gaps. `--empty 1` sends from the home screen, `--height 520` makes the page scroll to make room, `--png` draws the path, `--css` injects rules first (what does it cost without the halo? `.send-flight-halo{display:none}`). A busy machine makes the gaps meaningless: compare against the committed flight served from a `git archive` copy on another port, alternating runs. |
| `flight-curve.mjs [--flights "-367,372;-650,110"] [--out file.png]` | The way and the speed of the flight with no page and no animation clock: `sendFlightMotion.ts` is compiled and run on its own and drawn as a 60 Hz screen would show it (a dot for every frame: bunched dots are slow; outlines of the capsule; the straight line it does not take) with a bar for the speed of every frame, and a table: the time, when the clock is at 97%, the top speed and when it comes, how long the speed stays within 80% of the top (a plateau reads as constant speed; about a seventh is right), how bent the path is, how fast the first frame is (it leaves from rest). It is the picture that showed the spring flight to be a plateau. Written up in `docs/send-arc-2026-10-18.md`. |
| `flight-film.mjs [--scene closeup,handoff,strip,onion] [--from field\|card\|steer] [--times 0,30,...] [--step 33] [--cols 3] [--theme dark] [--empty 1] [--zoom 1.4]` | Frame-exact pictures: the flight's animations *and every other animation on the page* are paused and seeked to the same time, so a frame is the whole screen at that moment. `closeup` crops each frame around the capsule (the skin, the shadow, the text's crispness), `handoff` puts the ghost at its last moment beside the real bubble with a difference image and numbers, `strip` is the lower part of the page at each time, `onion` overlays a frame every `--step` ms into one picture (the path and the spacing along it; `--step 16.667` is what a 60 Hz screen shows). `--from card` sends from a starter card of the home screen, `--from steer` sends guidance into a running task. The `::placeholder` animation is not listed by `getAnimations()`, so the field may show a placeholder in the first frames. The `handoff` frame is read 0.1 ms before the end (two milliseconds before, any 0.01 px of offset flips the glyphs' sub-pixel bins and shows two hundred pixels that are not there). Written up in `docs/send-arc-2026-10-18.md` (and `docs/send-lift-2026-10-17.md`, `docs/send-capsule-2026-10-15.md`, `docs/send-flight-2026-10-14.md`). |

Tests: `scripts/tests/icon-motion.test.mjs` (static: names, parts, ownership, dead lucide selectors) and
`scripts/tests/icon-motion-browser.mjs` (real hover, focus, reduced motion, rows and tools, loaders, press).
