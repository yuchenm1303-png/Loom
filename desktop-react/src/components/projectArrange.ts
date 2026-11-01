/* How the build hub's cards (ProjectFlow.tsx) make room for each other.
 *
 * The columns lay the cards out by themselves. When that layout changes (a card arrives or
 * leaves, one grows), it changes on one clock:
 *   - every card that changed place glides there from where it was seen last (FLIP on transform);
 *   - a card that arrives, leaves or grows is clipped, every frame, to the room its neighbours
 *     leave it: it unrolls into the gap as they part and is folded away as they close in, so no
 *     card is ever painted over another one; its surface hangs from the top of that room, so its head
 *     shows first and the rows unroll under it (and, leaving, roll up in reverse, head last);
 *   - the cables are re-measured each frame against what is visible, so they stay plugged in.
 * A card that leaves is lifted out of its column at the place it was seen and held there (its
 * column may re-centre under it) while it folds and fades. */
import { useLayoutEffect, useRef } from "react";
import { DUR, EASE } from "../motion/tokens";
import { onReducedMotionChange, prefersReducedMotionNow } from "../motion/useReducedMotion";

/** One clock for every layout move of the hub; the size changes in project-flow.css run on the same step. */
export const HUB_CLOCK = DUR.panel;

type Place = { x: number; y: number; width: number; height: number };
type Edges = { top: number; bottom: number };
type Kind = "arrive" | "leave" | "grow";
interface Reveal {
  node: HTMLElement;
  kind: Kind;
  prev?: HTMLElement;
  next?: HTMLElement;
  /** The room kept between this card and each neighbour (stage px), from the layout it is going to (or leaving). */
  gapPrev: number;
  gapNext: number;
  /** An edge without a neighbour travels on the clock, from `from` to `to` (stage px). */
  from: Edges;
  to: Edges;
  /** The corner of the card's surface, which the cut keeps. */
  round: number;
  /** What moves with the top of the cut: the card's surface, or the parts of a card that is its own slot. */
  hung: HTMLElement[];
}

// What each card shows now, in client px; the cables plug into this, not into the clipped-away part.
const shownEdges = new WeakMap<HTMLElement, Edges>();

/** The part of a card that is seen: its box, cut to the room its neighbours leave it while the layout moves. */
export function visibleBox(node: HTMLElement) {
  const box = node.getBoundingClientRect();
  const slot = node.closest<HTMLElement>("[data-glide]");
  const edges = slot && shownEdges.get(slot);
  if (!edges) return box;
  const top = Math.max(box.top, edges.top), bottom = Math.max(top, Math.min(box.bottom, edges.bottom));
  return new DOMRect(box.left, top, box.width, bottom - top);
}

/** Holds a leaving card out of the flow at the place it was seen, whatever its column does meanwhile. */
function pin(node: HTMLElement, from: Place, root: HTMLElement) {
  const holder = (node.offsetParent as HTMLElement | null) ?? node.parentElement;
  if (!holder) return;
  const frame = holder.getBoundingClientRect(), base = root.getBoundingClientRect();
  node.dataset.pinned = "1";
  Object.assign(node.style, {
    position: "absolute", gridArea: "auto", margin: "0", zIndex: "0",
    left: `${base.left + from.x - frame.left - holder.clientLeft + holder.scrollLeft}px`,
    top: `${base.top + from.y - frame.top - holder.clientTop + holder.scrollTop}px`,
    width: `${from.width}px`, height: `${from.height}px`,
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

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

/** Cards glide from where they were seen to where the layout put them whenever `key` changes.
 *  The old places are read while rendering the change (the DOM is still the old one then), so a
 *  card caught mid-glide starts again from where it is, not from where it was headed. */
export function useGlide(stage: React.RefObject<HTMLElement | null>, key: string, onFrame: () => void) {
  const seen = useRef(key);
  const before = useRef(new Map<string, Place>());
  if (key !== seen.current && stage.current) before.current = places(stage.current);
  const frame = useRef(0);
  const targets = useRef(new WeakMap<HTMLElement, { x: number; y: number }>());
  const held = useRef(new Map<HTMLElement, Place>());  // leaving cards and the place they are held at
  const reveals = useRef<Reveal[]>([]);
  const clock = useRef<Animation | null>(null);

  const clear = () => {
    for (const { node, hung } of reveals.current) {
      node.style.clipPath = "";
      shownEdges.delete(node);
      for (const part of hung) part.style.translate = "";
    }
    reveals.current = [];
  };
  useLayoutEffect(() => () => { cancelAnimationFrame(frame.current); clock.current?.cancel(); }, []);
  useLayoutEffect(() => onReducedMotionChange(() => {
    if (!prefersReducedMotionNow()) return;
    cancelAnimationFrame(frame.current);
    for (const animation of stage.current?.getAnimations({ subtree: true }) ?? []) {
      if (animation.id === "pf-glide") animation.cancel();
    }
    clear();
    onFrame();
  }), [stage, onFrame]);

  useLayoutEffect(() => {
    if (key === seen.current) return;
    seen.current = key;
    const root = stage.current;
    if (!root) return;
    const nodes = [...root.querySelectorAll<HTMLElement>("[data-glide]")];
    for (const node of [...held.current.keys()]) if (!node.isConnected) held.current.delete(node);
    // A card that comes back while still leaving takes its place in the column again.
    for (const node of nodes) if (node.dataset.pinned && !node.hasAttribute("data-leaving")) { unpin(node); held.current.delete(node); }
    // A card that leaves is lifted out of the column where it stands, so the others make its room
    // now, while it folds away, not after it has gone: one movement, not two in a row.
    for (const node of nodes) {
      if (!node.hasAttribute("data-leaving") || held.current.has(node)) continue;
      const from = before.current.get(node.dataset.glide!);
      if (from) held.current.set(node, from);
    }
    // Removing a flex item also recentres its column: every held card is placed again against the new column.
    for (const [node, from] of held.current) pin(node, from, root);
    const reduce = prefersReducedMotionNow();
    if (reduce) {
      for (const node of nodes) for (const running of node.getAnimations()) if (running.id === "pf-glide") running.cancel();
      clear();
      onFrame();
      return;
    }

    // Where every card is going (its layout box), with the transform of an unfinished glide taken off.
    const base = root.getBoundingClientRect();
    const final = new Map<HTMLElement, Place>();
    for (const node of nodes) {
      const box = node.getBoundingClientRect();
      const matrix = node.dataset.pinned ? null : new DOMMatrixReadOnly(getComputedStyle(node).transform);
      final.set(node, { x: box.left - base.left - (matrix?.e ?? 0), y: box.top - base.top - (matrix?.f ?? 0), width: box.width, height: box.height });
    }

    let moved = false;
    for (const node of nodes) {
      if (node.dataset.pinned) continue;
      const target = final.get(node)!;
      const running = node.getAnimations().find((animation) => animation.id === "pf-glide");
      const previousTarget = targets.current.get(node);
      targets.current.set(node, target);
      // A status update need not restart a card already going to this place.
      if (running && previousTarget && Math.hypot(target.x - previousTarget.x, target.y - previousTarget.y) < 0.5) { moved = true; continue; }
      running?.cancel();
      const from = before.current.get(node.dataset.glide!);
      if (!from) continue;
      const dx = from.x - target.x, dy = from.y - target.y;
      if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) continue;
      node.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }], { duration: HUB_CLOCK, easing: EASE.move }).id = "pf-glide";
      moved = true;
    }

    // Who reveals what: the cards that arrive, leave or grew, each between its neighbours in its column.
    const previous = new Map(reveals.current.map((reveal) => [reveal.node, shownEdges.get(reveal.node)]));
    for (const { node, hung } of reveals.current) { node.style.clipPath = ""; for (const part of hung) part.style.translate = ""; }
    const next: Reveal[] = [];
    const staying = (node: HTMLElement) => !node.hasAttribute("data-leaving");
    const columnOf = (node: HTMLElement) => node.parentElement;
    const startOf = (node: HTMLElement): Place | undefined => before.current.get(node.dataset.glide!);
    for (const node of nodes) {
      const from = startOf(node), to = final.get(node)!;
      // A card called back while it was folding away unrolls again from what it still showed.
      const kind: Kind | null = node.hasAttribute("data-leaving") ? (held.current.has(node) ? "leave" : null)
        : !from || previous.has(node) ? "arrive" : to.height > from.height + 0.5 ? "grow" : null;
      if (!kind) continue;
      let prev: HTMLElement | undefined, after: HTMLElement | undefined;
      if (columnOf(node)?.classList.contains("pf-col")) {
        const siblings = [...columnOf(node)!.children].filter((item): item is HTMLElement =>
          item instanceof HTMLElement && item.hasAttribute("data-glide") && (item === node || staying(item)));
        const index = siblings.indexOf(node);
        prev = siblings[index - 1];
        after = siblings[index + 1];
      } else {
        // The timeline spans the stage under the columns: the lowest card above it is its neighbour.
        const above = nodes.filter((item) => item !== node && staying(item) && item.closest(".pf-col"));
        const low = (item: HTMLElement) => { const place = kind === "leave" ? startOf(item) ?? final.get(item)! : final.get(item)!; return place.y + place.height; };
        prev = above.sort((a, b) => low(b) - low(a))[0];
      }
      // The room to a neighbour as the layout keeps it: the one being left for a leave, the one being made otherwise.
      const placeOf = (item: HTMLElement, side: "start" | "end") => (side === "start" ? startOf(item) : undefined) ?? final.get(item)!;
      const side = kind === "leave" ? "start" : "end";
      const own = kind === "leave" ? from! : to;
      const gapPrev = prev ? own.y - (placeOf(prev, side).y + placeOf(prev, side).height) : 0;
      const gapNext = after ? placeOf(after, side).y - (own.y + own.height) : 0;
      // The edges without a neighbour: an arriving card unrolls out of the neighbour it has, a leaving one
      // is folded into it; alone, it opens from (and closes to) its middle.
      const middle = own.y + own.height / 2;
      const prevStart = prev && placeOf(prev, "start"), prevEnd = prev && final.get(prev)!;
      const nextStart = after && placeOf(after, "start"), nextEnd = after && final.get(after)!;
      const seam = (start: boolean) => prev ? (start ? prevStart! : prevEnd!).y + (start ? prevStart! : prevEnd!).height + gapPrev
        : after ? (start ? nextStart! : nextEnd!).y - gapNext : middle;
      const fromEdges: Edges = kind === "arrive" ? { top: seam(true), bottom: seam(true) } : { top: own.y, bottom: own.y + own.height };
      const toEdges: Edges = kind === "leave" ? { top: seam(false), bottom: seam(false) } : { top: to.y, bottom: to.y + to.height };
      // A card caught mid-reveal goes on from what it showed.
      if (previous.has(node)) {
        const shown = previous.get(node);
        const box = from ?? to;  // nothing cut away: it showed its whole box
        fromEdges.top = shown ? shown.top - base.top : box.y;
        fromEdges.bottom = shown ? shown.bottom - base.top : box.y + box.height;
      }
      const surface = node.querySelector<HTMLElement>(":scope > .pf-card") ?? node;
      const round = parseFloat(getComputedStyle(surface).borderTopLeftRadius) || 0;
      const hung = surface === node ? [...node.children].filter((part): part is HTMLElement => part instanceof HTMLElement) : [surface];
      next.push({ node, kind, prev, next: after, gapPrev, gapNext, from: fromEdges, to: toEdges, round, hung });
    }
    reveals.current = next;
    if (!moved && !next.length && !held.current.size) { onFrame(); return; }

    clock.current?.cancel();
    clock.current = new Animation(new KeyframeEffect(null, [], { duration: HUB_CLOCK, easing: EASE.move, fill: "forwards" }));
    clock.current.play();
    const tick = () => {
      const timing = clock.current?.effect?.getComputedTiming();
      const t = Number(timing?.progress ?? 1);
      const now = root.getBoundingClientRect();
      for (const [node, from] of held.current) if (node.isConnected) pin(node, from, root);
      // Reads first, then writes: a frame lays the hub out once.
      const cuts = reveals.current.map((reveal) => {
        const box = reveal.node.getBoundingClientRect();
        const top = reveal.prev
          ? reveal.prev.getBoundingClientRect().bottom + reveal.gapPrev
          : reveal.kind === "grow" ? box.top : now.top + lerp(reveal.from.top, reveal.to.top, t);
        const bottom = reveal.next
          ? reveal.next.getBoundingClientRect().top - reveal.gapNext
          : reveal.kind === "grow" ? box.bottom : now.top + lerp(reveal.from.bottom, reveal.to.bottom, t);
        return { reveal, box, top, bottom };
      });
      for (const { reveal, box, top, bottom } of cuts) {
        const above = Math.max(0, top - box.top), below = Math.max(0, box.bottom - bottom);
        // An arriving or leaving card hangs from the top of its room: its head shows first and goes last.
        if (reveal.kind !== "grow") for (const part of reveal.hung) part.style.translate = above < 0.5 ? "" : `0 ${above.toFixed(1)}px`;
        if (above < 0.5 && below < 0.5) { reveal.node.style.clipPath = ""; shownEdges.delete(reveal.node); continue; }
        // The card's own corners go with the cut, so a card unrolls as a card, not as a slice of one.
        reveal.node.style.clipPath = `inset(${above.toFixed(1)}px 0 ${below.toFixed(1)}px 0 round ${reveal.round}px)`;
        shownEdges.set(reveal.node, { top, bottom });
      }
      onFrame();
      if (t < 1) frame.current = requestAnimationFrame(tick);
      else clear();
    };
    cancelAnimationFrame(frame.current);
    tick();
  });
}
