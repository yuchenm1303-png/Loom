# Long-conversation interaction performance — 2026-10-08

The reported symptom was smooth history scrolling but slow buttons/cards,
streaming text, and animations once the conversation became long. The earlier
fixture had many folded tool records but short final answers; it did not cover
the large amount of rich Markdown in this case.

## Confirmed causes

1. The global scrollbar manager refreshed every registered scrollport on **any**
   `transitionend` event. A button's color transition therefore measured every
   historical code block. Auto-follow during streaming also repositioned all
   historical scrollports, including those far outside the viewport.
2. Pointer contrast changed an inherited cursor variable on `html` when crossing
   light/dark surfaces. That invalidated cursor styles throughout the transcript
   during an otherwise local interaction.

## Changes

- Track visible scrollports with `IntersectionObserver`; skip geometry reads for
  offscreen targets, and unregister them when their DOM is removed.
- Scope color/shadow/opacity transition handling to affected ancestor/descendant
  scrollports. Geometry transitions and portal fades still refresh all visible
  controls, preserving sibling positioning and overlay occlusion.
- Scope pointer contrast to the hit element, including the animated native
  cursor's tone selection. Keep theme defaults and resolution preloading.

History contents, Markdown parsing, native selection/copy, and the scroll-follow
policy remain intact. No message virtualization or height estimates were added.

## Browser evidence

The new fixture mounts 20 long, completed replies with prose, lists, tables, and
syntax-highlighted code: approximately **22,463 DOM elements and 320 code blocks**.
Animations are enabled for the interaction/streaming regression.

| Workload | Before | After |
| --- | ---: | ---: |
| 20 button color transition completions: code geometry reads | 12,800 | 0 |
| 20 small scroll steps: code geometry reads | 12,800 | 56–60 |
| 20 cursor contrast switches: style recalculation time | 4,076 ms at root | 5.7 ms locally |
| 20 streaming updates: offscreen historical code geometry reads | 25,520 | 0 |

Times are synthetic headless-Chrome measurements on this machine, not a guarantee
of an installed application's frame rate. Counter-based checks enforce bounded
work; the cursor benchmark compares the same DOM with root vs local ownership,
restoring the original root rules' specificity so both sides actually change
the cursor resource. The 4,076 ms is the sum of 20 switches, not one switch.

Passed typecheck, renderer production build, and browser checks for:

- Long rich history, actual feedback-button hover/click, input, streaming
  completion, bottom anchoring, and conversation switching.
- Cursor contrast, preload assets, animation frames, and reduced motion.
- Scrollbar drag/keyboard, nested lanes, dynamic overflow, and unmount cleanup.
- Existing renderer history teardown, background-update isolation, bounded
  inspector events, and repeated thread switching.
- Existing pointer-light discovery performance regression.

The regression is `desktop-react/scripts/tests/long-conversation-browser.mjs`.
Set `LOOM_TEST_ORIGIN`, `LOOM_CHROMIUM_PATH`, and `LOOM_PLAYWRIGHT_MODULE` as for
the existing browser tests. Optional `LOOM_LONG_CONVERSATION_BASELINE` points to
a previous `customScrollbars.ts` source file for the before/after comparison.
