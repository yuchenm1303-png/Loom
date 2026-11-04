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
import { DUR, EASE, SPRING, STAGGER_MS } from "../motion/tokens";
import { onReducedMotionChange, prefersReducedMotionNow } from "../motion/useReducedMotion";

/** One clock for every layout move of the hub; the size changes in project-flow.css run on the same step and curve.
 *  The settle spring starts from rest and lands without a crawl: the old move curve jumped 14% in the first
 *  frame and spent its last 270 ms on the final fifth. Its overshoot is half a percent, under a pixel here. */
export const HUB_CLOCK = DUR.panel;
export const HUB_EASE = SPRING.settle;
/** How far apart the cards of a column set off when they move together. */
const RIPPLE_MS = STAGGER_MS / 2;
/** The rows of an arriving card, which come in one after another as it unrolls. */
const ROWS = ".pf-well, .pf-task, .pf-loom-lane, .pf-model-option, .pf-empty";
const MAX_ROWS = 8;
/** An arriving card comes in three beats. Its cable grows out of the hub first (project-flow.css); when the cable
 *  is most of the way there, the card's bare surface opens out of its tip; then what it holds fades in, the rows
 *  one after another, and the small marks (counts, clocks) last. */
const SHELL_LEAD = DUR.fast;
/** The contents wait until the surface is nearly full size: text fades in, it is never seen stretching. */
const CONTENT_LAG = STAGGER_MS * 3;
const MARKS = ".pf-count, .pf-agent-time, .pf-agent-check, .pf-add";
/** Several cards arriving in one column together come out a little apart and spring into line. */
const BATCH_OFFSET = 12, BATCH_STEP = 5;
const ENTRANCE_IDS = new Set(["pf-glide", "pf-row", "pf-shell", "pf-settle", "pf-part", "pf-mark"]);

/** The three beats of an arriving card (its surface, then its parts, then its marks). Returns how long they run. */
function enter(surface: HTMLElement, lead: number, batch: { index: number; side: number } | null) {
  surface.animate([{ opacity: 0 }, { opacity: 1 }], { duration: DUR.base, delay: lead, easing: EASE.out, fill: "backwards" }).id = "pf-shell";
  // Out of the tip: wider first (it is still bare), then to full height as its rows unroll.
  surface.animate([{ scale: "0.7 0.94" }, { scale: "1" }], { duration: DUR.base, delay: lead, easing: HUB_EASE, fill: "backwards" }).id = "pf-shell";
  const content = lead + CONTENT_LAG;
  for (const part of surface.children) {
    if (part instanceof HTMLElement) part.animate([{ opacity: 0 }, { opacity: 1 }], { duration: DUR.base, delay: content, easing: EASE.out, fill: "backwards" }).id = "pf-part";
  }
  const rows = [...surface.querySelectorAll<HTMLElement>(ROWS)].slice(0, MAX_ROWS);
  rows.forEach((row, index) => {
    row.animate([{ opacity: 0 }, { opacity: 1 }], { duration: DUR.base, delay: content + (index + 1) * STAGGER_MS, easing: EASE.out, fill: "backwards" }).id = "pf-row";
  });
  const marks = content + (rows.length + 1) * STAGGER_MS;
  for (const mark of surface.querySelectorAll<HTMLElement>(MARKS)) {
    mark.animate([{ opacity: 0, scale: "0.6" }, { opacity: 1, scale: "1" }], { duration: DUR.fast, delay: marks, easing: EASE.out, fill: "backwards" }).id = "pf-mark";
  }
  let end = Math.max(lead + DUR.base, marks + DUR.fast);
  if (batch && batch.side) {
    // Out from the hub by a little more for each card down the column, then in on a spring that overshoots once.
    const dx = batch.side * (BATCH_OFFSET + batch.index * BATCH_STEP);
    surface.animate([{ transform: `translateX(${dx}px)` }, { transform: "none" }], { duration: DUR.slow, delay: lead, easing: SPRING.lively, fill: "backwards" }).id = "pf-settle";
    end = Math.max(end, lead + DUR.slow);
  }
  return end;
}

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
  /** An arriving card next to one that leaves takes over its room: it unrolls against the leaving card's
   *  shown edge (the column's gap apart) as that card rolls up, so the two never share a pixel. */
  gap: number;
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
export function useGlide(stage: React.RefObject<HTMLElement | null>, key: string, onFrame: (fresh?: boolean) => void) {
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
      if (ENTRANCE_IDS.has(animation.id)) animation.cancel();
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
    const started: { node: HTMLElement; target: Place; dy: number }[] = [];
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
      started.push({ node, target, dy });
      node.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }], { duration: HUB_CLOCK, easing: HUB_EASE, fill: "backwards" }).id = "pf-glide";
      moved = true;
    }
    // A ripple: cards of one column that travel the same way set off one after another, the one in front first.
    // In front means ahead in the direction of travel, so the gaps between them only open on the way and no card
    // runs into the one ahead of it. Closing in, the cards nearest the change go first; making room, the farthest.
    let lastDelay = 0;
    const groups = new Map<string, typeof started>();
    for (const item of started) {
      const column = item.node.parentElement;
      if (!column?.classList.contains("pf-col") || Math.abs(item.dy) < 0.5) continue;
      const key = `${[...column.parentElement!.children].indexOf(column)}:${item.dy < 0 ? "down" : "up"}`;
      groups.set(key, [...(groups.get(key) ?? []), item]);
    }
    for (const [key, group] of groups) {
      const down = key.endsWith("down");
      group.sort((a, b) => down ? b.target.y - a.target.y : a.target.y - b.target.y);
      group.forEach(({ node }, index) => {
        if (!index) return;
        const glide = node.getAnimations().find((animation) => animation.id === "pf-glide");
        glide?.effect?.updateTiming({ delay: index * RIPPLE_MS });
        lastDelay = Math.max(lastDelay, index * RIPPLE_MS);
      });
    }

    // Who reveals what: the cards that arrive, leave or grew, each between its neighbours in its column.
    const previous = new Map(reveals.current.map((reveal) => [reveal.node, shownEdges.get(reveal.node)]));
    for (const { node, hung } of reveals.current) { node.style.clipPath = ""; for (const part of hung) part.style.translate = ""; }
    const next: Reveal[] = [];
    const staying = (node: HTMLElement) => !node.hasAttribute("data-leaving");
    const columnOf = (node: HTMLElement) => node.parentElement;
    const startOf = (node: HTMLElement): Place | undefined => before.current.get(node.dataset.glide!);
    // A card called back while it was folding away unrolls again from what it still showed.
    const kindOf = new Map<HTMLElement, Kind>();
    for (const node of nodes) {
      const from = startOf(node), to = final.get(node)!;
      const kind: Kind | null = node.hasAttribute("data-leaving") ? (held.current.has(node) ? "leave" : null)
        : !from || previous.has(node) ? "arrive" : to.height > from.height + 0.5 ? "grow" : null;
      if (kind) kindOf.set(node, kind);
    }
    for (const node of nodes) {
      const from = startOf(node), to = final.get(node)!;
      const kind = kindOf.get(node);
      if (!kind) continue;
      let prev: HTMLElement | undefined, after: HTMLElement | undefined;
      // A leaving card handed over to the card arriving next to it folds toward that card's side's far end:
      // "up" when the newcomer is below it (it rolls up into its head), "down" when the newcomer is above.
      let handover: "up" | "down" | null = null;
      let gap = 0;
      if (columnOf(node)?.classList.contains("pf-col")) {
        const column = columnOf(node)!;
        gap = parseFloat(getComputedStyle(column).rowGap) || 0;
        const all = [...column.children].filter((item): item is HTMLElement => item instanceof HTMLElement && item.hasAttribute("data-glide"));
        // An arriving card's neighbours may be leaving (it unrolls against them); a leaving or growing card's
        // neighbours are the cards that stay put in the layout, never a newcomer (which has no room of its own yet).
        const siblings = all.filter((item) => item === node
          || (kind === "arrive" ? staying(item) || kindOf.get(item) === "leave" : staying(item) && kindOf.get(item) !== "arrive"));
        const index = siblings.indexOf(node);
        prev = siblings[index - 1];
        after = siblings[index + 1];
        if (kind === "leave") {
          const at = all.indexOf(node);
          if (kindOf.get(all[at + 1]) === "arrive") { handover = "up"; after = undefined; }
          else if (kindOf.get(all[at - 1]) === "arrive") { handover = "down"; prev = undefined; }
        }
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
      const fold = handover === "up" ? own.y : handover === "down" ? own.y + own.height : seam(false);
      const toEdges: Edges = kind === "leave" ? { top: fold, bottom: fold } : { top: to.y, bottom: to.y + to.height };
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
      const leavingPrev = prev && kindOf.get(prev) === "leave", leavingNext = after && kindOf.get(after) === "leave";
      next.push({ node, kind, prev, next: after, gapPrev: leavingPrev ? gap : gapPrev, gapNext: leavingNext ? gap : gapNext,
                  from: fromEdges, to: toEdges, round, hung, gap });
    }
    reveals.current = next;
    // The entrances: a card that has a cable waits for it; cards arriving together in one column follow each other
    // down it. A card with no surface of its own (the timeline) and one called back mid-exit only fade their rows.
    const arrivals = new Map<Element | null, Reveal[]>();
    for (const reveal of next) {
      if (reveal.kind !== "arrive" || previous.has(reveal.node)) continue;
      arrivals.set(columnOf(reveal.node), [...(arrivals.get(columnOf(reveal.node)) ?? []), reveal]);
    }
    for (const group of arrivals.values()) {
      group.sort((a, b) => final.get(a.node)!.y - final.get(b.node)!.y);
      group.forEach(({ node }, index) => {
        const surface = node.querySelector<HTMLElement>(":scope > .pf-card");
        if (!surface) {
          [...node.querySelectorAll<HTMLElement>(ROWS)].slice(0, MAX_ROWS).forEach((row, at) => {
            row.animate([{ opacity: 0 }, { opacity: 1 }], { duration: DUR.base, delay: (at + 1) * STAGGER_MS, easing: EASE.out, fill: "backwards" }).id = "pf-row";
          });
          return;
        }
        const side = surface.dataset.side === "right" ? 1 : surface.dataset.side === "left" ? -1 : 0;
        const together = group.length > 1;
        const end = enter(surface, SHELL_LEAD + (together ? index * STAGGER_MS : 0), together ? { index, side } : null);
        lastDelay = Math.max(lastDelay, end - HUB_CLOCK);
      });
    }
    // `fresh`: the first frame of a new layout, from which the cables ease their plugs to it.
    if (!moved && !next.length && !held.current.size) { onFrame(true); return; }

    clock.current?.cancel();
    clock.current = new Animation(new KeyframeEffect(null, [], { duration: HUB_CLOCK, easing: HUB_EASE, fill: "forwards" }));
    // The cuts follow live neighbours, so the frames go on until the last card of a ripple has landed.
    const until = performance.now() + HUB_CLOCK + lastDelay;
    clock.current.play();
    let first = true;
    const tick = () => {
      const timing = clock.current?.effect?.getComputedTiming();
      const t = Number(timing?.progress ?? 1);
      const now = root.getBoundingClientRect();
      for (const [node, from] of held.current) if (node.isConnected) pin(node, from, root);
      // Reads first, then writes: a frame lays the hub out once. The cards that leave are cut first: a newcomer
      // next to one unrolls against what it still shows this frame (its box stays whole while it is held).
      const shown = new Map<HTMLElement, Edges>();
      const edge = (item: HTMLElement, side: "top" | "bottom") => shown.get(item)?.[side] ?? item.getBoundingClientRect()[side];
      // Against a card that leaves, the gap closes with its last pixels, so the newcomer ends exactly in its place.
      const apart = (item: HTMLElement, gap: number) => {
        const cut = shown.get(item);
        return cut && item.hasAttribute("data-leaving") ? Math.min(gap, cut.bottom - cut.top) : gap;
      };
      // A leaving neighbour that has been taken out of the page leaves its side to the clock.
      const live = (item?: HTMLElement) => item && item.isConnected ? item : undefined;
      const order = [...reveals.current].sort((a, b) => Number(a.kind === "arrive") - Number(b.kind === "arrive"));
      const cuts = order.map((reveal) => {
        const box = reveal.node.getBoundingClientRect();
        const prev = live(reveal.prev), after = live(reveal.next);
        const top = prev
          ? edge(prev, "bottom") + apart(prev, reveal.gapPrev)
          : reveal.kind === "grow" ? box.top : now.top + lerp(reveal.from.top, reveal.to.top, t);
        const bottom = after
          ? edge(after, "top") - apart(after, reveal.gapNext)
          : reveal.kind === "grow" ? box.bottom : now.top + lerp(reveal.from.bottom, reveal.to.bottom, t);
        const cut = { top: Math.max(box.top, top), bottom: Math.max(Math.max(box.top, top), Math.min(box.bottom, bottom)) };
        shown.set(reveal.node, cut);
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
      onFrame(first);
      first = false;
      if (t < 1 || performance.now() < until) frame.current = requestAnimationFrame(tick);
      else clear();
    };
    cancelAnimationFrame(frame.current);
    tick();
  });
}
