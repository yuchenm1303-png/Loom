# Long-conversation scrollbar discovery - 2026-10-19

Historical tool processes already unmount after folding, and completed turns
use `content-visibility: auto`. Historical final answers still retain their
Markdown DOM so selection, copying and browser text search remain available.

The remaining scrollbar discovery path bypassed this optimization: an ancestor
class change walked every descendant and called `getComputedStyle` on every
unregistered scroll candidate. Real triggers include `is-running` on the
conversation stage and `loom-panel-motion` on the body. Most Markdown containers
never become scrollports, so every discovery pass measured them again.

Discovery now rejects distant completed-turn subtrees before walking their
contents. An IntersectionObserver schedules discovery within 600px of the
viewport. Active, settling and newest turns remain immediate. Removed turns
release their observer registrations and retained references. Existing visible
scrollport geometry tracking and dynamic overflow discovery remain in place.

The regression uses 20 long rich-text replies with 320 code blocks. Six paired
ancestor class changes produced **7,068 historical computed-style reads before
the change and 0 after it**, while positioned at the newest reply. This counts
work in the synthetic browser fixture, not installed-app FPS or latency.

Run `desktop-react/scripts/tests/long-conversation-discovery-browser.mjs` with
the browser environment described in `desktop-react/README.md`. It also checks
that older code registers on scrolling into history, scrolls horizontally, and
that switching away and restoring the conversation pins to the bottom.
`LOOM_DISCOVERY_BASELINE=1` logs counts without enforcing the new work bound when
running against the old implementation.

This change does not virtualize Markdown or bound the number of loaded turns.
Very large individual replies still incur Markdown parsing and DOM creation;
this fix specifically removes repeated scrollbar discovery across distant
historical replies.

Validation passed: TypeScript checks, renderer production build, the new
discovery regression, existing long-conversation hover/scroll/streaming checks,
dynamic scrollbar overflow, transcript follow/disclosure ownership, and renderer
resource checks over 12 long-history switches. The separate
`disclosure-motion-browser.mjs` fails at line 79 because a composer control
selector returns null; rerunning with the unmodified `HEAD` scrollbar source
reproduced the same failure. Its preceding process/step/reasoning disclosure
checks passed before that unrelated composer-control step.

## Follow-up: hidden reasoning and repeated highlighting

Collapsed reasoning still mounted its entire Markdown renderer, including every
table, formula and code block, and reparsed changes while invisible. It now
mounts only when opened, retains its contents for the existing closing animation,
then releases them. Content is frozen during the exit. Reopening immediately
shows the latest canonical reasoning rather than replaying a typing backlog.
Closed content is inert, and reasoning does not retain presentation snapshots
after unmount. Static Markdown messages no longer each subscribe to document
visibility events; receiving/painting messages still handle background recovery.

`reasoning-performance-browser.mjs` covers 20 completed turns, each with a long
reasoning field. Restoring eager reasoning rendering with its baseline route
creates **23,114 elements and 20 hidden Markdown surfaces**; lazy rendering
creates **1,634 elements and zero hidden Markdown surfaces**, about 93% fewer
elements in this fixture. Both runs use the same highlighting implementation.
These are fixture node counts, not an overall memory or FPS claim. The test
also checks current text after reopening, animated closing, exit reversal,
reduced motion, desktop/mobile viewports and visibility-listener cleanup.

The existing shared syntax highlighter now reuses unchanged code tokens across
Markdown parses. It uses the existing retained-budget map, limited to 128
entries, 2 MiB of estimated retained data and 256 KiB per entry. Source keys
count toward the budget; blocks over 32,768 source characters bypass caching.
Cache hits promote the existing entry without re-estimating its AST size.
Token trees are cloned to prevent cross-message mutation, and source positions
remain local to each parse. Unknown-language diagnostics and unusual plugin
output follow the original highlighter. No runtime dependency was added.

The cache regression checks changed content/languages, clone isolation, source
positions, original output/diagnostics, count and byte eviction, oversized
entries, and 32 unchanged blocks across 20 streaming updates: **32 actual
highlight operations instead of 640**. Markdown parsing itself still runs for
the full visible message; this is not an incremental Markdown parser.

Follow-up validation: 47 focused unit tests, typecheck, renderer build, Markdown
processing, streaming lifecycle (including tables, interruption and background
recovery), long-conversation interactions, transcript follow and 12 repeated
history switches. The old streaming fixture was missing its I18n provider;
it was corrected so the lifecycle regression runs against the current renderer.
