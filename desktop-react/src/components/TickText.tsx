import { useEffect, useState } from "react";
import { useReducedMotion } from "../motion/useReducedMotion";
import { DUR } from "../motion/tokens";
import { diffCharacters, type Ghost } from "./tickDiff";
import "./tick-text.css";

/**
 * Text that changes one character at a time, as a running counter does: only the characters that differ swap.
 * The old one blurs out and the new one blurs in on the same spot (opacity and blur, nothing moves), while the
 * rest of the text stays still. Keep numbers tabular so a swapped digit lands where the old one was.
 */
export function TickText({ value, className = "" }: { value: string; className?: string }) {
  const reduce = useReducedMotion();
  const [state, setState] = useState<{ value: string; ghosts: Ghost[]; fresh: number[]; whole: string; revision: number }>({ value, ghosts: [], fresh: [], whole: "", revision: 0 });
  let current = state;
  if (value !== state.value) {
    const { ghosts, fresh, whole } = diffCharacters(state.value, value);
    current = { value, ghosts: reduce ? [] : ghosts, fresh: reduce ? [] : fresh, whole: reduce ? "" : whole ?? "", revision: state.revision + 1 };
    setState(current);
  }
  // The outgoing characters only need to exist while they fade; the incoming ones keep their mark until the next change.
  useEffect(() => {
    if (!state.ghosts.length && !state.whole) return;
    const timer = window.setTimeout(() => setState((now) => ({ ...now, ghosts: [], whole: "" })), DUR.base);
    return () => window.clearTimeout(timer);
  }, [state.revision, state.ghosts.length, state.whole]);

  return (
    <span className={`tick-text ${className}`.trim()}>
      <span className="sr-only">{current.value}</span>
      {current.whole ? <span className="tick-ghost" key={`whole:${current.revision}`} aria-hidden="true">{current.whole}</span> : null}
      {Array.from(current.value).map((char, index) => {
        const ghost = current.ghosts.find((item) => item.index === index);
        const animating = current.fresh.includes(index);
        return (
          <span className="tick-slot" key={index} aria-hidden="true">
            {ghost ? <span className="tick-ghost" key={`ghost:${current.revision}`}>{ghost.char}</span> : null}
            <span className={animating ? "tick-in" : undefined} key={animating ? `${char}:${current.revision}` : "still"}>{char}</span>
          </span>
        );
      })}
    </span>
  );
}
