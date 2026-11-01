/* How the build hub's cards (ProjectFlow.tsx) make room for each other.
 *
 * The columns lay the cards out by themselves. When that layout changes (a card arrives or
 * leaves, one grows), every card that changed place glides there from where it was seen last
 * (FLIP on transform), and the cables are re-measured each frame of the glide so they stay
 * plugged in. */
import { useLayoutEffect, useRef } from "react";
import { DUR, EASE, travelMs } from "../motion/tokens";
import { prefersReducedMotionNow } from "../motion/useReducedMotion";

/** Takes a leaving card out of the flow at the place it is seen (mid-glide included), in its containing block. */
function pin(node: HTMLElement) {
  const holder = (node.offsetParent as HTMLElement | null) ?? node.parentElement;
  if (!holder) return;
  const box = node.getBoundingClientRect(), frame = holder.getBoundingClientRect();
  for (const running of node.getAnimations()) if (running.id === "pf-glide") running.cancel();
  node.dataset.pinned = "1";
  Object.assign(node.style, {
    position: "absolute", gridArea: "auto", margin: "0", zIndex: "0",
    left: `${box.left - frame.left - holder.clientLeft}px`, top: `${box.top - frame.top - holder.clientTop}px`,
    width: `${box.width}px`, height: `${box.height}px`,
  });
}

function unpin(node: HTMLElement) {
  delete node.dataset.pinned;
  for (const name of ["position", "gridArea", "margin", "zIndex", "left", "top", "width", "height"] as const) node.style[name] = "";
}

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
    const nodes = [...root.querySelectorAll<HTMLElement>("[data-glide]")];
    // A card that comes back while still leaving takes its place in the column again.
    for (const node of nodes) if (node.dataset.pinned && !node.hasAttribute("data-leaving")) unpin(node);
    // A card that leaves is lifted out of the column where it stands, to fade there, so the others
    // make its room now, while it fades, not after it has gone: one movement, not two in a row.
    for (const node of nodes) if (node.hasAttribute("data-leaving") && !node.dataset.pinned) pin(node);
    const base = root.getBoundingClientRect();
    let longest = 0;
    const arriving: HTMLElement[] = [];
    for (const node of nodes) {
      if (node.dataset.pinned) continue;
      for (const running of node.getAnimations()) if (running.id === "pf-glide") running.cancel();
      const from = before.current.get(node.dataset.glide!);
      if (!from) { arriving.push(node); continue; }
      const box = node.getBoundingClientRect();
      const dx = from.x - (box.left - base.left), dy = from.y - (box.top - base.top);
      if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) continue;
      const duration = travelMs(Math.hypot(dx, dy));
      const glide = node.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }], { duration, easing: EASE.move });
      glide.id = "pf-glide";
      longest = Math.max(longest, duration);
    }
    if (!longest) return;
    // An arriving card waits until the others have mostly made its room (its cable is already
    // growing toward it), so it never fades in over a card still moving out of its way.
    for (const node of arriving) {
      node.animate([{ opacity: 0 }, { opacity: 1 }], { duration: DUR.base, delay: Math.round(longest * 0.45), easing: EASE.out, fill: "backwards" }).id = "pf-arrive";
    }
    // The cables follow the cards for as long as any of them is still on its way.
    cancelAnimationFrame(frame.current);
    const end = performance.now() + longest;
    const tick = () => { onFrame(); if (performance.now() < end) frame.current = requestAnimationFrame(tick); };
    tick();
  });
}
