import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useReducedMotion } from "../motion/useReducedMotion";
import { RUN_PHASE_PRESENTATION_HOLD_MS } from "../presentationTiming";
import "./crossfade.css";

/** Retain the outgoing paint while the new surface fades in at the same origin. */
export function Crossfade({ identity, children, className = "", block = false }: {
  identity: string; children: ReactNode; className?: string; block?: boolean;
}) {
  const reduce = useReducedMotion();
  const latest = useRef(children);
  const [state, setState] = useState<{ identity: string; outgoing: ReactNode; revision: number }>({ identity, outgoing: null, revision: 0 });
  let current = state;
  if (identity !== state.identity) {
    current = { identity, outgoing: reduce ? null : latest.current, revision: state.revision + 1 };
    setState(current);
  }
  latest.current = children;
  useEffect(() => {
    if (!state.outgoing) return;
    if (reduce) { setState(value => ({ ...value, outgoing: null })); return; }
    const timer = window.setTimeout(() => setState(value => ({ ...value, outgoing: null })), RUN_PHASE_PRESENTATION_HOLD_MS);
    return () => clearTimeout(timer);
  }, [state.identity, state.outgoing, reduce]);
  const Tag = block ? "div" : "span";
  return <Tag className={`crossfade ${className}`} style={{ "--crossfade-ms": `${RUN_PHASE_PRESENTATION_HOLD_MS}ms` } as CSSProperties}>
    {current.outgoing && !reduce ? <Tag key={`out-${current.revision}`} className="crossfade-out" aria-hidden="true" inert>{current.outgoing}</Tag> : null}
    <Tag key={current.revision} className={current.outgoing && !reduce ? "crossfade-in" : "crossfade-current"}>{children}</Tag>
  </Tag>;
}
