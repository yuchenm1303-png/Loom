/* How the build hub's cards (ProjectFlow.tsx) make room for each other.
 *
 * The columns lay the cards out by themselves. When that layout changes (a card arrives or
 * leaves, one grows), every card that changed place glides there from where it was seen last
 * (FLIP on transform), and the cables are re-measured each frame of the glide so they stay
 * plugged in. */
import { useLayoutEffect, useRef } from "react";
import { DUR, EASE, travelMs } from "../motion/tokens";
import { onReducedMotionChange, prefersReducedMotionNow } from "../motion/useReducedMotion";

type Place = { x: number; y: number; width: number; height: number };

/** Takes a leaving card out of the flow at the place it was seen, before its column reflowed. */
function pin(node: HTMLElement, from: Place | undefined, root: HTMLElement) {
  const holder = (node.offsetParent as HTMLElement | null) ?? node.parentElement;
  if (!holder) return;
  const box = node.getBoundingClientRect(), frame = holder.getBoundingClientRect();
  const base = root.getBoundingClientRect();
  for (const running of node.getAnimations()) if (running.id === "pf-glide") running.cancel();
  node.dataset.pinned = "1";
  Object.assign(node.style, {
    position: "absolute", gridArea: "auto", margin: "0", zIndex: "0",
    left: `${(from ? base.left + from.x : box.left) - frame.left - holder.clientLeft + holder.scrollLeft}px`,
    top: `${(from ? base.top + from.y : box.top) - frame.top - holder.clientTop + holder.scrollTop}px`,
    width: `${from?.width ?? box.width}px`, height: `${from?.height ?? box.height}px`,
  });
}

function unpin(node: HTMLElement) {
  delete node.dataset.pinned;
  for (const name of ["position", "gridArea", "margin", "zIndex", "left", "top", "width", "height"] as const) node.style[name] = "";
}

function places(root: HTMLElement) {
  const base = root.getBoundingClientRect();
  const out = new Map<string, Place>();
  for (const node of root.querySelectorAll<HTMLElement>("[data-glide]")) {
    const box = node.getBoundingClientRect();
    out.set(node.dataset.glide!, { x: box.left - base.left, y: box.top - base.top, width: box.width, height: box.height });
  }
  return out;
}

/** Cards glide from where they were seen to where the layout put them whenever `key` changes.
 *  The old places are read while rendering the change (the DOM is still the old one then), so a
 *  card caught mid-glide starts again from where it is, not from where it was headed. */
export function useGlide(stage: React.RefObject<HTMLElement | null>, key: string, onFrame: () => void) {
  const seen = useRef(key);
  const before = useRef(new Map<string, Place>());
  if (key !== seen.current && stage.current) before.current = places(stage.current);
  const frame = useRef(0);
  const targets = useRef(new WeakMap<HTMLElement, { x: number; y: number }>());
  useLayoutEffect(() => () => cancelAnimationFrame(frame.current), []);
  useLayoutEffect(() => onReducedMotionChange(() => {
    if (!prefersReducedMotionNow()) return;
    cancelAnimationFrame(frame.current);
    for (const animation of stage.current?.getAnimations({ subtree: true }) ?? []) {
      if (animation.id === "pf-glide") animation.cancel();
    }
    onFrame();
  }), [stage, onFrame]);
  useLayoutEffect(() => {
    if (key === seen.current) return;
    seen.current = key;
    const root = stage.current;
    if (!root) return;
    const nodes = [...root.querySelectorAll<HTMLElement>("[data-glide]")];
    // A card that comes back while still leaving takes its place in the column again.
    for (const node of nodes) if (node.dataset.pinned && !node.hasAttribute("data-leaving")) unpin(node);
    // A card that leaves is lifted out of the column where it stands, to fade there, so the others
    // make its room now, while it fades, not after it has gone: one movement, not two in a row.
    for (const node of nodes) if (node.hasAttribute("data-leaving") && !node.dataset.pinned) pin(node, before.current.get(node.dataset.glide!), root);
    // Removing a flex item also recentres its column. Resolve the pinned coordinates again
    // against that final column, otherwise the disappearing card jumps with the column.
    for (const node of nodes) if (node.dataset.pinned) pin(node, before.current.get(node.dataset.glide!), root);
    if (prefersReducedMotionNow()) {
      for (const node of nodes) for (const running of node.getAnimations()) if (running.id === "pf-glide") running.cancel();
      onFrame();
      return;
    }
    const base = root.getBoundingClientRect();
    let longest: number = DUR.flow;
    for (const node of nodes) {
      if (node.dataset.pinned) continue;
      const running = node.getAnimations().find((animation) => animation.id === "pf-glide");
      const visual = node.getBoundingClientRect();
      const matrix = new DOMMatrixReadOnly(getComputedStyle(node).transform);
      const target = { x: visual.left - base.left - matrix.e, y: visual.top - base.top - matrix.f };
      const previousTarget = targets.current.get(node);
      targets.current.set(node, target);
      // A status update or exit cleanup need not restart a card already going to this place.
      if (running && previousTarget && Math.hypot(target.x - previousTarget.x, target.y - previousTarget.y) < 0.5) {
        longest = Math.max(longest, Number(running.effect?.getTiming().duration ?? 0) - Number(running.currentTime ?? 0));
        continue;
      }
      running?.cancel();
      const from = before.current.get(node.dataset.glide!);
      if (!from) { longest = Math.max(longest, DUR.flow); continue; }
      const box = node.getBoundingClientRect();
      const dx = from.x - (box.left - base.left), dy = from.y - (box.top - base.top);
      if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) continue;
      const duration = travelMs(Math.hypot(dx, dy));
      const glide = node.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }], { duration, easing: EASE.move });
      glide.id = "pf-glide";
      longest = Math.max(longest, duration);
    }
    // The inner surface owns arrival/exit. Keeping it separate from the slot's FLIP transform
    // lets a rapid reversal continue its opacity and short travel without fighting the reorder.
    // The cables follow the cards for as long as any of them is still on its way.
    cancelAnimationFrame(frame.current);
    const end = performance.now() + longest;
    const tick = () => { onFrame(); if (performance.now() < end) frame.current = requestAnimationFrame(tick); };
    tick();
  });
}
