import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useReducedMotion } from "../motion/useReducedMotion";
import { DUR } from "../motion/tokens";
import "./crossfade.css";

/** Retain the outgoing paint while the new surface fades in at the same origin. `soft` blurs the two as they cross
 *  (opacity and blur, nothing moves): for a line of text that changes its words, as a status does. */
export function Crossfade({ identity, children, className = "", block = false, soft = false }: {
  identity: string; children: ReactNode; className?: string; block?: boolean; soft?: boolean;
}) {
  const ms = soft ? DUR.flow : DUR.base;
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
    const timer = window.setTimeout(() => setState(value => ({ ...value, outgoing: null })), ms);
    return () => clearTimeout(timer);
  }, [state.identity, state.outgoing, reduce, ms]);
  const Tag = block ? "div" : "span";
  return <Tag className={`crossfade${soft ? " crossfade--soft" : ""} ${className}`} style={{ "--crossfade-ms": `${ms}ms` } as CSSProperties}>
    {current.outgoing && !reduce ? <Tag key={`out-${current.revision}`} className="crossfade-out" aria-hidden="true" inert>{current.outgoing}</Tag> : null}
    <Tag key={current.revision} className={current.outgoing && !reduce ? "crossfade-in" : "crossfade-current"}>{children}</Tag>
  </Tag>;
}
