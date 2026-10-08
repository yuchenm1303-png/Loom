# Loom performance audit — 2026-10-07

The reported slowdown was not explained by system memory pressure alone. A
long conversation accumulated hidden React/DOM trees after the user expanded
and closed historical turns. The execution inspector also constructed every
loaded event when opened. Global cursor resource updates (fixed separately in
6b42890d) amplified the cost of those large trees on clicks.

## Measured comparison

The same production-built App fixture, styles, and Chromium executable were
used before and after the Transcript/Inspector changes. The fixture contains
20 completed turns with 100 tool records each. All turns were expanded and
closed before opening the inspector. No live accounts or model requests were
used. DOM numbers below count connected elements; heap numbers are JavaScript
heap after an explicit browser GC, not total Electron process memory.

| Scenario | Before | After |
| --- | ---: | ---: |
| Cold folded conversation, connected elements | 1,750 | 1,750 |
| Visited and folded history, connected elements | 32,090 | 1,750 |
| Inspector open, connected elements | 66,069 | 2,163 |
| Inspector open, JS heap | 44.7 MB | 22.4 MB |
| Inspector closed, JS heap | 35.3 MB | 22.8 MB |

Repeatedly reopening the same turn twelve times did not grow DOM counters or
listeners after the first visit (development build: approximately 4,043–4,045
GC-counted nodes and 622 listeners). Some detached nodes are retained by React;
this check does not claim that all heap returns to the cold-start value.

A separate local backend fixture had twenty snapshots with 100 large assistant
messages each. Two hundred full session reads took 1,036 ms; two hundred warm
overview reads took 218 ms. Cold/changed snapshots are still parsed normally.

## Changes

- Retain folded content only through its exit animation, including reversal;
  then unmount the hidden history. Reopening reconstructs it from durable data.
- Render execution events within the viewport plus eight rows of overscan.
  Measure expanded rows, retain correct total scroll extent, and keep all
  records accessible by scrolling. Tab switches reset the viewport.
- Bound navigation snapshots by estimated retained bytes as well as count:
  sixteen MiB total, eight MiB per entry, up to eight conversations. Larger
  conversations still open normally but are not retained after navigation.
- Cache small sidebar overviews for up to 128 sessions, validated against file
  size/timestamps/inode under the existing recovery lock. Store only metadata,
  usage and an 80-character title; do not retain images, prompts, tool bindings
  or model context. Runtime execution continues to use complete sessions.
- Memoize the pet and its static vector contours so conversation stream updates
  do not rebuild decorative artwork. Pet state changes remain interactive.

## Validation

- Renderer performance regression: all historical folds release connected DOM;
  inspector rows remain bounded; first/last events and expanded output remain
  accessible; background notifications do not revisit active history; twelve
  long-history switches do not accumulate DOM/listeners; new chat tears down
  history; exit reversal remains intact.
- Earlier-process and runtime-motion regressions: reversible folds, live tool
  protection, consecutive batches, stable streaming, reduced motion and IME.
- Streaming regression: long Markdown tables, scroll detach/return, final
  relocation, interrupted output, history and StrictMode.
- Pet regression: poses, petting, typing, drag/keyboard movement, task state,
  approval navigation, focus, sleep/wake, reduced motion and narrow layouts.
- Backend suites: overview invalidation/isolation, titles, navigation, projects,
  presentation projections, context reporting, local IPC, protocol parity and
  durable recovery.
- TypeScript checks, retained-budget unit tests and production renderer build.

Browser scripts use `LOOM_PLAYWRIGHT_MODULE`, `LOOM_CHROMIUM_PATH` and optional
`LOOM_TEST_ORIGIN`, consistent with existing repository tests. Key regressions
are `renderer-performance-browser.mjs`, `earlier-process-browser.mjs`,
`runtime-motion-browser.mjs`, `streaming-browser.mjs`, `retained-budget.test.mjs`
and `tests/test_session_overview.py`.

## Scope and remaining limits

The audit covered transcript loading/rendering, collapsed content, inspector
scrolling, stream presentation, scroll following, event isolation, navigation
caches, session listing, preference listeners, pet redraws and Electron IPC/HUD
lifetime. Existing paths already page history, share motion preference observers,
skip hidden inspector trees, assemble IPC frames once and lazily create the HUD.

Cold event reconstruction still scans durable event history; a single very
large expanded message still requires parsing/rendering its content. The initial
renderer bundle remains roughly 1.8 MB JS and 1.2 MB CSS before gzip. Further
startup reductions would require separate module/style splitting measurements.
Electron, Python, browsers and active model/tools retain their own baseline
process memory. This audit does not certify every feature/hardware combination
as maximally lightweight or establish a frame rate for the running installed
application. The installed application must load the rebuilt renderer and Host
runtime before its user-visible performance can be compared.
