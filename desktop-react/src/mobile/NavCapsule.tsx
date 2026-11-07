import { useLayoutEffect, useRef } from "react";
import { useReducedMotion } from "../motion/useReducedMotion";

/** One travelling surface; a press never paints a second pill under the destination. */
export function NavCapsule({ index }: { index: number }) {
  const pill = useRef<HTMLSpanElement>(null);
  const animation = useRef<Animation | null>(null);
  const previous = useRef(index);
  const reduced = useReducedMotion();
  useLayoutEffect(() => {
    const element = pill.current;
    if (!element) return;
    const from = getComputedStyle(element).transform;
    animation.current?.cancel();
    const distance = Math.abs(index - previous.current);
    previous.current = index;
    if (reduced || !distance) return;
    animation.current = element.animate([
      { transform: from === "none" ? "scale(1)" : from, offset: 0 },
      { transform: `scale(${1.18 + Math.min(distance, 3) * .045}, .86)`, offset: .27 },
      { transform: "scale(.95, 1.045)", offset: .76 },
      { transform: "scale(1)", offset: 1 },
    ], { duration: 480, easing: "cubic-bezier(.22,.7,.22,1)" });
  }, [index, reduced]);
  useLayoutEffect(() => () => { animation.current?.cancel(); }, []);
  return <i className="m-glider" aria-hidden="true"><span className="m-nav-pill" ref={pill} /></i>;
}
