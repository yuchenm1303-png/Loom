# Performance follow-up — 2026-10-08

This pass continued the transcript/cache audit with page-wide DOM observers,
project refresh timers, Markdown processing, startup dependencies and Electron
timers. The changes below address observed avoidable work; this is not a claim
that every feature or hardware configuration is maximally lightweight.

## Page-wide scrollbar discovery

The custom scrollbar observer treated every inline `style` mutation as a reason
to rediscover every descendant scrollport. Updating an ancestor's transform,
height or opacity therefore repeatedly inspected all the historical content.

Ordinary inline style changes now inspect only the changed element and update
existing descendant overlays. Class changes, custom property changes and `all`
changes retain subtree discovery because they can change descendant overflow
rules. Nested discovery roots in one mutation batch are deduplicated. Removed
scrollports still disconnect observers and remove their controls.

The browser regression mounts 10,000 descendant elements, settles initialization,
then updates the ancestor transform twenty times. It compares the previous
module against the changed module in the same fixture/browser, instruments
`getComputedStyle`, and reads Chromium Performance domain counters.

| Counter over the measured window | Previous | Changed |
| --- | ---: | ---: |
| Style reads within the test subtree | 200,020 | 20 |
| Main-thread TaskDuration | 221.6 ms | 52.8 ms |
| ScriptDuration | 191.4 ms | 7.1 ms |

These are synthetic, instrumented development-fixture measurements. They are
not frame-rate measurements of the installed app, total process CPU, or an
end-to-end speedup guarantee. Two animation frames separate each mutation, so
elapsed wall time is dominated by browser scheduling and is not the assertion.
The regression also exercises descendant overflow enabled by ancestor classes
and custom properties. Existing drag, wheel/keyboard, nested-lane, dynamic
overflow and teardown behavior passes its original browser suite.

Reproduce with `scrollbar-performance-browser.mjs`. An optional
`LOOM_SCROLLBAR_BASELINE` path serves an old TypeScript module via Playwright's
route interception for comparison, without replacing workspace files.

## Hidden-window and slow repository reads

ProjectGitBar and the open ProjectDetailsPanel previously used fixed intervals,
including in hidden windows. Requests could overlap when the Host or Git was
slow. They now use a shared visible polling scheduler:

- Hidden windows have no scheduled timer and start no new repository reads.
- Returning to a visible window refreshes immediately.
- A pending request must settle before another starts; the next interval starts
  after completion. Visibility changes cannot create duplicate in-flight reads.
- Disposal removes the visibility listener and prevents timer rescheduling.
- Errors retain the existing presentation policy and allow later refreshes.

An already-running request is allowed to complete. Runtime execution, terminal
output polling, authentication renewal and update checks are unchanged.
`visible-polling.test.mjs` covers hidden mount, visibility reversal during a slow
read, periodic refresh, failure retry and disposal.

## Markdown grammar registration

UserRichText reattached rehype-highlight for each render, re-registering its
common code grammars each time. User and assistant Markdown now share one lazy,
stateless highlighter transformer. UserRichText also skips parent-driven renders
when its text is unchanged. Syntax highlighting still runs for changed code.
The previously supplied `ignoreMissing` option is unsupported by the installed
rehype-highlight version and had no effect; unknown languages retain the
plugin's existing plain-code fallback.

The Markdown browser check verifies user/assistant code highlighting, shared
transformer identity, unknown languages, math, tables, link cards and the
existing protection against auto-fetching remote images in user messages.

## Validation and remaining work

TypeScript and production renderer build pass. Twelve cache/streaming/polling
unit tests pass. Browser regressions pass for scrollbar performance, window
chrome, Markdown processing, streaming, runtime motion and the 2,000-tool-event
renderer fixture. Repeated conversation switches retain stable listener counts;
visited folded history still returns from 1,858 to 1,859 connected elements.

The renderer still ships approximately 1.79 MB JavaScript and 1.22 MB CSS before
gzip (606 KB and 288 KB compressed). Optional-page splitting is a remaining
startup opportunity, but needs CSS cascade and first-open measurements before
changing eager imports. Large expanded messages still parse their full content.
The current desktop installation must load the updated renderer before real
window interaction can be compared; this pass does not restart active tasks or
publish/install an application release.
