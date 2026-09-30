// Settings navigation motion (the parts that need JavaScript).
//
// The page itself is choreographed in settings-page-motion.css. Once per
// navigation this module marks the freshly mounted page's top-level blocks and
// stamps data-flow on the persistent surface; CSS lifts the marked blocks into
// place. What CSS cannot know is where the sidebar highlight is travelling, so
// that lives here too: the highlight glides, stretches a little while it is fast,
// and lights its rail when it lands.
//
// Cost discipline. Navigation is the moment the user is waiting on the app, so
// nothing here runs in the click task or its layout effect except a couple of
// inline-style writes:
//   - the distance between the old and the new row is measured at click time,
//     while the layout is still clean (prepareNavTravel);
//   - everything that reads styles or starts animations waits for the next
//     animation frame, i.e. it runs inside the frame that has to resolve those
//     styles anyway, instead of forcing an early style recalc of the whole new page;
//   - the per-block custom properties are registered as non-inherited, so
//     setting them never restyles a block's descendants.

/** How long the marks stay. Longer than the slowest block, so the cascade always
 *  finishes, but short enough that later inserts never replay it. */
export const PAGE_FLOW_MS = 900;

/** A block that mounts later than this after the page is left alone: its entrance
 *  would not finish before the marks come down (the slowest block takes ~520 ms). */
const LATE_BLOCK_WINDOW_MS = PAGE_FLOW_MS - 520;

/** Start delay of the nth block: heading first, then each section, closing up. */
export const PAGE_FLOW_LADDER_MS = [0, 44, 80, 112, 140, 164, 184] as const;

export type PageFlow = "forward" | "backward";

export function reducedMotionPreferred(): boolean {
  return document.documentElement.dataset.loomReducedMotion === "true"
    || Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
}

/** Longer jumps carry the page a little further, so distance is felt. */
export function pageFlowDistance(travel: number): number {
  return Math.round(Math.min(22, 12 + Math.abs(travel) / 40));
}

/** How long the highlight takes to get where it is going. */
function pillDuration(distance: number): number {
  return Math.round(300 + Math.min(160, distance * 0.5));
}

/**
 * Click-time bookkeeping for a navigation to `nextPage`, done while the layout is
 * clean: how far the highlight has to travel (positive = down) and, on the row
 * that is about to become active, when the pill will get there. That row only
 * takes its "selected" colours at that moment (see settings-page-motion.css); the
 * variable has to be in place before the first style recalc that sees `.active`.
 *
 * The trip starts where the pill *visibly is* (its centre, animation included),
 * so clicking again while it is still gliding carries on from mid-air instead of
 * snapping back to the row it was heading for.
 */
export function prepareNavTravel(nav: HTMLElement | null, nextPage: string): number {
  nav?.querySelectorAll<HTMLElement>("button[style*='--nav-arrive']").forEach((row) => row.style.removeProperty("--nav-arrive"));
  const fromRow = nav?.querySelector<HTMLElement>("button.active");
  const toRow = nav?.querySelector<HTMLElement>(`button[data-nav="${nextPage}"]`);
  if (!fromRow || !toRow) return 0;
  const from = (fromRow.querySelector<HTMLElement>(".settings-nav-pip") ?? fromRow).getBoundingClientRect();
  const to = toRow.getBoundingClientRect();
  const travel = (to.top + to.height / 2) - (from.top + from.height / 2);
  if (Math.abs(travel) >= 1 && !reducedMotionPreferred()) {
    toRow.style.setProperty("--nav-arrive", `${Math.round(pillDuration(Math.abs(travel)) * 0.55)}ms`);
  }
  return travel;
}

/** The blocks that make the entrance, and the element they are children of: the
 *  surface itself, or, for pages that wrap everything in one container (models,
 *  memory, web search, connectors), that container. Only blocks that start inside
 *  the viewport take part; the rest are not on screen to be animated and would
 *  only cost a compositor layer each.
 *
 *  This is the one place that reads layout. It runs in the animation frame, where
 *  the browser is about to lay the page out anyway, so it moves that work
 *  rather than adding to it; a zero-height block is a hidden one. */
function flowBlocks(surface: HTMLElement): { container: HTMLElement; blocks: HTMLElement[] } {
  const bottom = surface.closest(".settings-main-scroll")?.getBoundingClientRect().bottom ?? window.innerHeight;
  const shown = (nodes: Element[]) => nodes.filter((node): node is HTMLElement =>
    node instanceof HTMLElement
    // The update card is portaled into General and unmounts a frame after leaving it.
    && !node.classList.contains("software-update-section")
    && node.getBoundingClientRect().height > 0);
  const onScreen = (nodes: HTMLElement[]) => nodes.filter((node) => node.getBoundingClientRect().top < bottom);
  const top = shown(Array.from(surface.children));
  const wrapper = top.length === 1 && !top[0].classList.contains("settings-page-heading") ? top[0] : null;
  return wrapper
    ? { container: wrapper, blocks: onScreen(shown(Array.from(wrapper.children))) }
    : { container: surface, blocks: onScreen(top) };
}

const delayFor = (index: number) => PAGE_FLOW_LADDER_MS[Math.min(index, PAGE_FLOW_LADDER_MS.length - 1)];

/**
 * Let the page that just mounted rise into place. Returns the function that
 * removes the marks again.
 *
 * Some blocks mount a moment after the page (the update card is portaled into
 * General, connectors wait for their list). While the marks are up they join the
 * cascade where the ladder would have put them, or at once if that moment has gone.
 */
function startPageFlow(surface: HTMLElement, flow: PageFlow, travel: number): () => void {
  const startedAt = performance.now();
  const distance = `${pageFlowDistance(travel)}px`;
  const { container, blocks } = flowBlocks(surface);

  const marked = new Set<HTMLElement>();
  const mark = (block: HTMLElement, delay: number) => {
    marked.add(block);
    block.dataset.flowBlock = "";
    block.style.setProperty("--flow-delay", `${Math.round(delay)}ms`);
    block.style.setProperty("--flow-distance", distance);
  };
  blocks.forEach((block, index) => mark(block, delayFor(index)));
  surface.dataset.flow = flow;

  const late = new MutationObserver((records) => {
    const elapsed = performance.now() - startedAt;
    if (elapsed > LATE_BLOCK_WINDOW_MS) return;
    for (const record of records) {
      record.addedNodes.forEach((node) => {
        if (node instanceof HTMLElement && !marked.has(node)) mark(node, Math.max(0, delayFor(marked.size) - elapsed));
      });
    }
  });
  late.observe(container, { childList: true });

  return () => {
    late.disconnect();
    delete surface.dataset.flow;
    marked.forEach((block) => {
      delete block.dataset.flowBlock;
      block.style.removeProperty("--flow-delay");
      block.style.removeProperty("--flow-distance");
    });
  };
}

// Quick to get going, long unhurried landing (Material's "emphasized decelerate").
const GLIDE = "cubic-bezier(.2, 0, 0, 1)";

/**
 * Glide the active row's highlight from where the previous one was.
 *
 * `travel` is how far the highlight moves, in px (positive = down). The pill is
 * text-free, so it may stretch: `translate` carries it along the glide curve while
 * `scale` elongates it through the fast middle of the trip. Two independent
 * animations on two independent properties, both compositor-only. When the pill
 * lands its rail flares and its icon takes a small bow.
 */
function playNavPillTravel(nav: HTMLElement | null, travel: number): void {
  const row = nav?.querySelector<HTMLElement>("button.active");
  const pip = row?.querySelector<HTMLElement>(".settings-nav-pip");
  const distance = Math.abs(travel);
  if (!row || !pip || distance < 1 || typeof pip.animate !== "function") return;

  const duration = pillDuration(distance);
  const stretch = Math.min(0.5, 0.12 + distance / 520);
  const arrival = Math.round(duration * 0.55);

  pip.animate(
    { translate: [`0 ${-travel}px`, "0 0"] },
    { duration, easing: GLIDE },
  );
  pip.animate(
    { scale: ["1 1", `1 ${1 + stretch}`, "1 1"], offset: [0, 0.35, 1] },
    { duration, easing: "ease-in-out" },
  );
  pip.animate(
    {
      boxShadow: [
        "0 0 0 0 color-mix(in srgb, var(--sr-accent) 0%, transparent)",
        "0 0 11px 2px color-mix(in srgb, var(--sr-accent) 62%, transparent)",
        "0 0 0 0 color-mix(in srgb, var(--sr-accent) 0%, transparent)",
      ],
      scale: ["1 .8", "2.1 1.25", "1 1"],
      offset: [0, 0.34, 1],
    },
    { duration: 560, delay: arrival, easing: "ease-out", fill: "backwards", pseudoElement: "::before" },
  );
  row.querySelector<SVGElement>(":scope > svg")?.animate(
    { scale: [".8", "1.2", "1"], offset: [0, 0.5, 1] },
    { duration: 460, delay: arrival, easing: "cubic-bezier(.3, 1.3, .5, 1)", fill: "backwards" },
  );
}

/**
 * Play the whole navigation: the highlight travels, the page rises in. Call it
 * from the layout effect that follows the page change; the work itself runs at
 * the start of the next frame (before that frame is painted, so the first thing
 * the user sees is already the start of the motion). Returns the cleanup.
 */
export function playPageTransition(surface: HTMLElement, nav: HTMLElement | null, flow: PageFlow, travel: number): () => void {
  let stop: (() => void) | null = null;
  let timer = 0;
  const frame = requestAnimationFrame(() => {
    playNavPillTravel(nav, travel);
    stop = startPageFlow(surface, flow, travel);
    timer = window.setTimeout(() => stop?.(), PAGE_FLOW_MS);
  });
  return () => {
    cancelAnimationFrame(frame);
    window.clearTimeout(timer);
    stop?.();
  };
}
