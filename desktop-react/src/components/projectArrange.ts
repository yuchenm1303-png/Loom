/* How the build hub's cards (ProjectFlow.tsx) make room for each other.
 *
 * The columns lay the cards out by themselves. When that layout changes (a card arrives or
 * leaves, one grows), every card that changed place glides there from where it was seen last
 * (FLIP on transform), and the cables are re-measured each frame of the glide so they stay
 * plugged in. */
import { useLayoutEffect, useRef } from "react";
import { EASE, travelMs } from "../motion/tokens";
import { prefersReducedMotionNow } from "../motion/useReducedMotion";

function places(root: HTMLElement) {
  const base = root.getBoundingClientRect();
  const out = new Map<string, { x: number; y: number }>();
  for (const node of root.querySelectorAll<HTMLElement>("[data-glide]")) {
    const box = node.getBoundingClientRect();
    out.set(node.dataset.glide!, { x: box.left - base.left, y: box.top - base.top });
  }
  return out;
}

/** Cards glide from where they were seen to where the layout put them whenever `key` changes.
 *  The old places are read while rendering the change (the DOM is still the old one then), so a
 *  card caught mid-glide starts again from where it is, not from where it was headed. */
export function useGlide(stage: React.RefObject<HTMLElement | null>, key: string, onFrame: () => void) {
  const seen = useRef(key);
  const before = useRef(new Map<string, { x: number; y: number }>());
  if (key !== seen.current && stage.current) before.current = places(stage.current);
  const frame = useRef(0);
  useLayoutEffect(() => () => cancelAnimationFrame(frame.current), []);
  useLayoutEffect(() => {
    if (key === seen.current) return;
    seen.current = key;
    const root = stage.current;
    if (!root || prefersReducedMotionNow()) return;
    const base = root.getBoundingClientRect();
    let longest = 0;
    for (const node of root.querySelectorAll<HTMLElement>("[data-glide]")) {
      for (const running of node.getAnimations()) if (running.id === "pf-glide") running.cancel();
      const from = before.current.get(node.dataset.glide!);
      if (!from) continue;  // a card just arriving fades in where it is
      const box = node.getBoundingClientRect();
      const dx = from.x - (box.left - base.left), dy = from.y - (box.top - base.top);
      if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) continue;
      const duration = travelMs(Math.hypot(dx, dy));
      const glide = node.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }], { duration, easing: EASE.move });
      glide.id = "pf-glide";
      longest = Math.max(longest, duration);
    }
    if (!longest) return;
    // The cables follow the cards for as long as any of them is still on its way.
    cancelAnimationFrame(frame.current);
    const end = performance.now() + longest;
    const tick = () => { onFrame(); if (performance.now() < end) frame.current = requestAnimationFrame(tick); };
    tick();
  });
}
