# Frame-scoped pointer grounding

UIA rectangles are virtual-desktop physical pixels. Both UIA hint points and
visual action points are normalized against the full current screenshot frame.
Uniform image encoding downscale does not change these points; neither DPI nor
scroll-region dimensions should be applied a second time.

The single-loop action schema exposes `frame_id` and `control_id`. A control
target requires the exact current frame ID, an enabled fully contained rectangle,
and no conflicting point. It resolves to a physical pointer action, not UIA
Invoke, retaining the Windows foreground/geometry/occlusion checks. A stale
explicit frame is rejected before input. Legacy point-only callers remain
compatible and still use the session's latest observation and existing guards.

Browser Pane/ToolBar/Window shells no longer consume the bounded collected
control list. Model hints prioritize interactive controls across the collected
list rather than taking its first 40 entries, expose IDs, and explicitly identify
incomplete semantic observations. Deadline bounds remain in place.

Input acceptance and pixel change are not application success. Existing pointer
no-effect/repeat protections remain; semantic verification is not upgraded merely
because a control ID was resolved. Application outcomes such as a counter
increment still require independent visible or semantic evidence.

Regression coverage includes shell saturation, stale/missing/disabled/clipped
targets, conflicting targets, physical-coordinate round trips at 96/120/144 DPI
and negative desktop origins. These are automated regressions, not a claim of
full real-desktop acceptance. Re-test the browser counter on a fresh screenshot,
including scrolling, window movement and occlusion, before full acceptance.
