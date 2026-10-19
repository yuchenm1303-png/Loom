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
