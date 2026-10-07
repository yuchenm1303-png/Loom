import { useLayoutEffect, useRef, useState } from "react";
import { useReducedMotion } from "./useReducedMotion";

export type MotionPresencePhase = "entering" | "entered" | "exiting";

/**
 * Keep transient UI mounted long enough to animate its exit.
 *
 * A lot of Loom's old surfaces animated only on mount and disappeared
 * immediately on close. Presence separates logical visibility from DOM
 * lifetime so dialogs, menus and full-page surfaces share one predictable
 * enter/exit contract.
 */
export function useMotionPresence(open: boolean, exitMs = 240, identity?: string | number | null): {
  mounted: boolean;
  phase: MotionPresencePhase;
} {
  const mountedRef = useRef(open);
  const [mounted, setMounted] = useState(open);
  const [phase, setPhase] = useState<MotionPresencePhase>(open ? "entering" : "exiting");
  const enteringRef = useRef(open);
  const identityRef = useRef(identity);
  const exitTimerRef = useRef<number | null>(null);
  const frameRef = useRef<number | null>(null);
  const reduce = useReducedMotion();

  useLayoutEffect(() => {
    if (exitTimerRef.current !== null) {
      window.clearTimeout(exitTimerRef.current);
      exitTimerRef.current = null;
    }
    if (frameRef.current !== null) {
      cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }

    if (open) {
      const arriving = !mountedRef.current || identityRef.current !== identity;
      identityRef.current = identity;
      if (!mountedRef.current) {
        mountedRef.current = true;
        setMounted(true);
      }
      // Reverse an interrupted exit from its current painted position. Sending
      // a retained surface back to "entering" would teleport it off-screen.
      if (reduce || document.hidden || (!arriving && !enteringRef.current)) {
        enteringRef.current = false;
        setPhase("entered");
        return;
      }
      setPhase("entering");
      enteringRef.current = true;
      frameRef.current = requestAnimationFrame(() => {
        frameRef.current = requestAnimationFrame(() => {
          frameRef.current = null;
          enteringRef.current = false;
          setPhase("entered");
        });
      });
      return;
    }

    if (!mountedRef.current) return;
    enteringRef.current = false;
    setPhase("exiting");
    if (reduce || document.hidden) {
      mountedRef.current = false;
      setMounted(false);
      return;
    }
    exitTimerRef.current = window.setTimeout(() => {
      exitTimerRef.current = null;
      mountedRef.current = false;
      setMounted(false);
    }, exitMs);
    // StrictMode and rapid toggles must cancel the previous lifecycle before
    // scheduling its replacement, including the second entrance frame.
    return () => {
      if (exitTimerRef.current !== null) window.clearTimeout(exitTimerRef.current);
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
      exitTimerRef.current = null;
      frameRef.current = null;
    };
  }, [exitMs, open, reduce, identity]);

  useLayoutEffect(() => () => {
    if (exitTimerRef.current !== null) window.clearTimeout(exitTimerRef.current);
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
  }, []);

  return { mounted, phase };
}
