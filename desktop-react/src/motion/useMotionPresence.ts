import { useEffect, useRef, useState } from "react";

export type MotionPresencePhase = "entering" | "entered" | "exiting";

function reducedMotion(): boolean {
  return document.documentElement.dataset.loomReducedMotion === "true"
    || Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
}

/**
 * Keep transient UI mounted long enough to animate its exit.
 *
 * A lot of Loom's old surfaces animated only on mount and disappeared
 * immediately on close. Presence separates logical visibility from DOM
 * lifetime so dialogs, menus and full-page surfaces share one predictable
 * enter/exit contract.
 */
export function useMotionPresence(open: boolean, exitMs = 180): {
  mounted: boolean;
  phase: MotionPresencePhase;
} {
  const mountedRef = useRef(open);
  const [mounted, setMounted] = useState(open);
  const [phase, setPhase] = useState<MotionPresencePhase>(open ? "entered" : "exiting");
  const exitTimerRef = useRef<number | null>(null);
  const frameRef = useRef<number | null>(null);

  useEffect(() => {
    if (exitTimerRef.current !== null) {
      window.clearTimeout(exitTimerRef.current);
      exitTimerRef.current = null;
    }
    if (frameRef.current !== null) {
      cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }

    if (open) {
      if (!mountedRef.current) {
        mountedRef.current = true;
        setMounted(true);
      }
      if (reducedMotion()) {
        setPhase("entered");
        return;
      }
      setPhase("entering");
      frameRef.current = requestAnimationFrame(() => {
        frameRef.current = null;
        setPhase("entered");
      });
      return;
    }

    if (!mountedRef.current) return;
    setPhase("exiting");
    if (reducedMotion()) {
      mountedRef.current = false;
      setMounted(false);
      return;
    }
    exitTimerRef.current = window.setTimeout(() => {
      exitTimerRef.current = null;
      mountedRef.current = false;
      setMounted(false);
    }, exitMs);
  }, [exitMs, open]);

  useEffect(() => () => {
    if (exitTimerRef.current !== null) window.clearTimeout(exitTimerRef.current);
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
  }, []);

  return { mounted, phase };
}
