import { useEffect, useRef, useState, type CSSProperties } from "react";
import { useReducedMotion } from "../motion/useReducedMotion";
import { DUR } from "../motion/tokens";
import "./roll-text.css";

/**
 * A line of words that changes by rolling: the old words are lifted away and the new ones come up from below to the
 * same place, character by character from the left (a wave, not a block). Measured off a recording of a status row:
 * each old character leaves in `press` while it rises about a third of its height and blurs; the new one starts a
 * beat after, from the same distance below, and takes `base`; the next character is 11ms behind, at most 280ms over
 * the whole line. The line keeps its place the whole time (nothing is made room for by layout), and at rest it is
 * one plain text node, so a list of many titles costs nothing.
 */
const NEW_AFTER_MS = 90;
const STEP_MS = 11;
const MAX_SPREAD_MS = 280;
const RETAIN_MARGIN = 35;

const spread = (length: number) => Math.min(STEP_MS, MAX_SPREAD_MS / Math.max(1, length - 1));

function Characters({ text, delay, step }: { text: string; delay: number; step: number }) {
  return <>{Array.from(text).map((char, index) => (
    <span className="roll-ch" key={index} style={{ animationDelay: `${Math.round(delay + index * step)}ms` } as CSSProperties}>{char}</span>
  ))}</>;
}

export function RollText({ identity, text, className = "" }: { identity: string; text: string; className?: string }) {
  const reduce = useReducedMotion();
  const latest = useRef(text);
  const [state, setState] = useState<{ identity: string; outgoing: string | null; revision: number }>({ identity, outgoing: null, revision: 0 });
  let current = state;
  if (identity !== state.identity) {
    current = { identity, outgoing: reduce ? null : latest.current, revision: state.revision + 1 };
    setState(current);
  }
  latest.current = text;
  const outgoing = current.outgoing;
  const rolling = outgoing !== null && !reduce;
  useEffect(() => {
    if (state.outgoing === null) return;
    const longest = Math.max(Array.from(state.outgoing).length, Array.from(latest.current).length);
    const ms = (longest - 1) * spread(longest) + NEW_AFTER_MS + DUR.base + RETAIN_MARGIN;
    const timer = window.setTimeout(() => setState((value) => ({ ...value, outgoing: null })), ms);
    return () => window.clearTimeout(timer);
  }, [state.identity, state.outgoing]);
  return (
    <span className={`roll ${className}`.trim()}>
      <span className="sr-only">{text}</span>
      {rolling ? <span key={`out-${current.revision}`} className="roll-line roll-out" aria-hidden="true"><Characters text={outgoing} delay={0} step={spread(Array.from(outgoing).length)} /></span> : null}
      <span key={`in-${current.revision}`} className={`roll-line${rolling ? " is-rising" : ""}`} aria-hidden="true">
        {rolling ? <Characters text={text} delay={NEW_AFTER_MS} step={spread(Array.from(text).length)} /> : text}
      </span>
    </span>
  );
}
