import { useLayoutEffect, useRef } from "react";
import { useReducedMotion } from "../motion/useReducedMotion";
import { runCapsule, translatedX, type CapsuleRun } from "./motion";

/** One travelling surface, on one spring: it stretches with its speed and is its own shape again when it stops. */
export function NavCapsule({ index, variant = "navigation" }: { index: number; variant?: "navigation" | "segment" }) {
  const glider = useRef<HTMLElement>(null);
  const pill = useRef<HTMLSpanElement>(null);
  const run = useRef<CapsuleRun | null>(null);
  const previous = useRef(index);
  const resting = useRef<number | null>(null);
  const reduced = useReducedMotion();
  useLayoutEffect(() => {
    const carrier = glider.current;
    const shape = pill.current;
    if (!carrier || !shape) return;
    if (resting.current === null) resting.current = translatedX(carrier);
    if (previous.current === index) return;
    previous.current = index;
    // Where it is painted: mid-flight if it is flying, otherwise where it last came to rest. The stylesheet has
    // already placed it at its new tab by now, so that is where it is going.
    const flying = run.current?.carrier.playState === "running";
    const from = flying ? translatedX(carrier) : resting.current;
    run.current?.carrier.cancel();
    run.current?.pill.cancel();
    const to = translatedX(carrier);
    resting.current = to;
    if (reduced) { run.current = null; return; }
    run.current = runCapsule({ carrier, pill: shape, from, to, width: shape.offsetWidth, previous: run.current });
  }, [index, reduced]);
  useLayoutEffect(() => () => { run.current?.carrier.cancel(); run.current?.pill.cancel(); }, []);
  return <i className="m-glider" aria-hidden="true" ref={glider}><span className={variant === "segment" ? "m-segment-pill" : "m-nav-pill"} ref={pill} /></i>;
}
