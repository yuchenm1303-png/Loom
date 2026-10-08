# Desktop performance review — 2026-10-08

Reviewed the renderer's notification batching, transcript memoization and history
windowing, scroll synchronization, page observers, pointer effects, and the Host's
thread-read/event-cache/session-overview paths. Fixed three concrete renderer
hotspots without changing durable runtime state or model execution.

## Findings and changes

- **Scrollbar discovery reacted to pointer-light coordinates.** Updating
  `--lens-x/y` or `--starter-x/y` caused a descendant scan and ancestor geometry
  updates even though those properties only position gradients. Ignore these
  four paint-only properties while retaining discovery for classes, unknown
  custom properties, and real layout changes.
- **The portal cursor kept rendering while stationary.** Its spring loop stopped
  only after the pointer left the page. Stop after the springs and dirty work
  settle; existing pointer, input, DOM, scroll, and resize events wake it again.
- **Settings translation rescanned all settings after every mutation.** Translate
  only changed text/attributes and added subtrees, and temporarily disconnect
  the observer during translation to avoid processing its own writes. A language
  switch still translates the entire settings panel.

## Evidence and validation

- Headless Chrome, 10,000 descendant elements, 20 updates to pointer-light
  coordinates and a transform: **210,041 computed-style reads before; 20 after**.
  The remaining reads inspect the transformed container, not its descendants.
  Pointer coordinates alone triggered zero reads after the fix.
- A 2,000-row settings fixture: ten text edits and one added subtree required
  only two `TreeWalker.nextNode` calls. Text, titles, placeholders, new content,
  and switching back to English were verified.
- Verified that the portal cursor stops drawing when stationary both freely and
  over a magnetic target, and resumes on movement, clicks, and content changes.
- Existing renderer performance regression passed: folded-history teardown,
  background-notification isolation, bounded inspector events, 12 repeated
  history switches, listener cleanup, and the new-conversation screen.
- Typecheck and renderer production build passed. Relevant Host tests passed:
  **22 tests** covering session overviews, event caching, conversation navigation,
  and streaming tool identities. The repository virtualenv launcher is broken on
  this machine; these tests used the bundled Python 3.12 interpreter with the
  existing virtualenv packages via process-local `PYTHONPATH`.

These are synthetic workload results, not an end-to-end measurement of the
installed application or model response latency. Existing unrelated workspace
changes were preserved and excluded from this performance commit.

Browser regression scripts:
`desktop-react/scripts/tests/scrollbar-performance-browser.mjs`,
`desktop-react/scripts/tests/runtime-effects-browser.mjs`, and
`desktop-react/scripts/tests/renderer-performance-browser.mjs`.
