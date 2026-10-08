# Permission, model and context popover response

The production permission/model popover was mounted at opacity zero and waited
for the presence hook's two animation frames before fading in (230 ms opacity
transition). ContextMeter had the same transparent entrance in `shell-fix.css`.
Its component stylesheet still contained an older 440 ms inset morph and
90–180 ms content staggers, even though shell-fix overrode them. The component
source alone did not describe production behavior.

The browser regression reproduced the permission menu at opacity zero on the
first animation frame, despite its trigger already reporting expanded and its
content being interactive. This is a confirmed visual response delay, not proof
of a slow provider request. Model discovery and active-thread refresh already
run asynchronously and are not awaited before opening.

Changes:

- Permission/model/expression popovers use a readable starting opacity of .72
  and a small transform at mount, with 100/160 ms opacity/transform transitions.
  `@starting-style` starts the entrance when mounted; the existing presence
  hook continues to own exit retention, unmount and reversal. The phase change
  no longer gates first-frame visibility.
- ContextMeter now owns one fixed-geometry surface and its motion. Remove the
  context section of shell-fix rather than layering a higher-priority override.
  Remove the obsolete pseudo-shell, content staggers and unused chip-width
  ResizeObserver/layout measurement. Keep the narrow-screen dimensions,
  scrolling, light/dark appearance and reduced-motion behavior.
- Keep exit durations below their existing presence retention durations.

Validation:

- Production App browser fixture in both themes, normal/reduced motion: all
  three menus have readable content on their first animation frame, without a
  content delay; rapid reversal and Escape eventually release each surface.
- Hold the model picker's authoritative thread refresh unresolved while opening
  and closing it: the cached model controls remain usable.
- Before the fix, the first-frame visibility assertion fails. After the fix it
  passes. Do not interpret headless Vite cold-render timings as installed-app
  latency; rendering cost and scheduling still affect when that frame occurs.
- Typechecking, renderer build, related Python contracts and workspace browser
  regressions cover the surrounding controls. CI runs the new browser test.

The old context exit source contract described a pseudo-shell that production
already hid. Replace that assertion with fixed-surface ownership/no-inset motion
and no shell-fix override, while retaining the existing exit timing assertions.
The real-browser test verifies the interaction behavior rather than relying
only on stylesheet text. Other tasks' workspace changes are not part of this fix.
