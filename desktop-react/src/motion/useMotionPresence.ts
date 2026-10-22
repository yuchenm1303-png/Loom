import { useLayoutEffect, useRef, useState } from "react";
import { RETAIN } from "./tokens";
import { useReducedMotion } from "./useReducedMotion";

/**
 * "entered" while the surface is open, "exiting" while it is closed and still mounted for its exit. A surface that
 * opens is mounted straight into "entered": where it comes from is written in its stylesheet as `@starting-style`,
 * so the browser starts the entrance on the very frame the surface first appears instead of two frames later.
 * ("entering" is kept in the type for stylesheets and callers that still name it; the hook never reports it.)
 */
export type MotionPresencePhase = "entering" | "entered" | "exiting";

/**
 * Keep transient UI mounted long enough to animate its exit.
 *
 * Presence separates logical visibility from DOM lifetime so dialogs, menus, docks and pages share one enter/exit
 * contract: open = mounted at once in "entered" (the stylesheet's `@starting-style` is the first frame of the
 * entrance), closed = "exiting" for `exitMs`, then unmounted. Reopening during the exit turns the surface around from
 * where it is painted (its transitions simply reverse).
 *
 * `exitMs` is how long the surface stays mounted once it is closed. It has to outlast the exit written in the
 * stylesheet, so pass one of the named values in motion/tokens.ts (`RETAIN.fast` for a popover, a menu or a dock,
 * `RETAIN.base` for what appears in place, `RETAIN.fold` for a fold): each is the exit of its step plus a margin. The
 * clock starts on the frame the exit starts, not when React commits the close, so a busy frame in between can delay
 * the exit but never cut it short. A literal number here is refused by tests/test_motion_scale_contract.py.
 *
 * `identity` is accepted for callers that swap what one surface shows; a new surface is a new element, and its own
 * `@starting-style` plays its entrance.
 */
export function useMotionPresence(open: boolean, exitMs: number = RETAIN.base, _identity?: string | number | null): {
  mounted: boolean;
  phase: MotionPresencePhase;
} {
  const reduce = useReducedMotion();
  // Whether the surface is still held for its exit. Derived during render from the change of `open`, so the render
  // that closes it already keeps it mounted (an effect would let it vanish for a frame first).
  const [previousOpen, setPreviousOpen] = useState(open);
  const [retained, setRetained] = useState(false);
  if (previousOpen !== open) {
    setPreviousOpen(open);
    setRetained(!open);
  }
  const frameRef = useRef<number | null>(null);
  const timerRef = useRef<number | null>(null);

  useLayoutEffect(() => {
    if (open || !retained) return;
    if (reduce || exitMs <= 0 || document.hidden) {
      setRetained(false);
      return;
    }
    // The exit transition starts with the style of the next frame; so does the clock.
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null;
      timerRef.current = window.setTimeout(() => {
        timerRef.current = null;
        setRetained(false);
      }, exitMs);
    });
    return () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      frameRef.current = null;
      timerRef.current = null;
    };
  }, [exitMs, open, reduce, retained]);

  return { mounted: open || retained, phase: open ? "entered" : "exiting" };
}
