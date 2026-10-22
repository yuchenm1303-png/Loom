import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";

/**
 * Entering a conversation mounts the turns the reader can see first; the older ones of the window follow once the page
 * has been quiet for a moment.
 *
 * A conversation is read as a window of up to twenty turns. Building all of them at once (the markdown of every
 * answer, then the layout of the page) was most of what a switch cost: a quarter of a second of one long task, of
 * which the turns above the screen were most. Each turn the first commit leaves out is about 40 ms the click does not
 * wait for (measured: two turns paint in ~60 ms, four in ~160 ms, all twenty in ~250 ms).
 *
 * The older turns are not dropped: they are mounted when the page has been quiet for a while, and at once when the
 * reader scrolls toward them, so the transcript ends up the same as before. The wait matters as much as the order: a
 * reader flipping through conversations leaves each before its older turns are due, and they are never built for it.
 * The view keeps its place meanwhile: it follows the live edge, which gives back the height each turn adds above it.
 */

/** Blocks the first commit of a conversation holds. */
export const INITIAL_TURN_BLOCKS = 2;
/** A conversation this much longer than that is not worth splitting: the few blocks it would leave out cost less than the second commit. */
export const SPARE_TURN_BLOCKS = 2;
/** A conversation whose first blocks do not fill this much of the screen mounts more before anything is painted. */
const FILL_VIEWPORT_FACTOR = 1.25;
/** Blocks added in one such step. */
const FILL_STEP = 2;
/** Blocks added by one idle slice. Every insertion restyles the transcript once whatever its size (about 8 ms), so slices are not tiny. */
export const IDLE_STEP = 3;
/** Blocks added when the reader scrolls within reach of the unmounted ones. */
const SCROLL_STEP = 3;
/** The page is left alone this long after a conversation opens before its older turns are built. */
export const BACKFILL_DELAY_MS = 1200;
/** A slice is taken within this long even when the page never goes idle. */
const IDLE_TIMEOUT_MS = 200;
/** The reader this close (in screens) to the first mounted block gets the next ones without waiting for idle. */
const SCROLL_REACH_SCREENS = 1.5;

/** Index of the first mounted block. A key that is no longer there mounts everything rather than hiding turns. */
export function mountedFrom(keys: readonly string[], startKey: string | null): number {
  if (startKey === null) return 0;
  const index = keys.indexOf(startKey);
  return index < 0 ? 0 : index;
}

/** The key the first mounted block has after `step` more blocks are mounted: null once the first block is reached. */
export function steppedStart(keys: readonly string[], from: number, step: number): string | null {
  const next = Math.max(0, from - step);
  return next === 0 ? null : keys[next];
}

/** The key a conversation opens at: its newest `count` blocks, or null when it has barely more than that. */
export function openingStart(keys: readonly string[], count: number = INITIAL_TURN_BLOCKS): string | null {
  return keys.length > count + SPARE_TURN_BLOCKS ? keys[keys.length - count] : null;
}

type Pending = { cancel(): void };

function whenIdle(run: () => void): Pending {
  const idle = window as Window & {
    requestIdleCallback?: (callback: () => void, options?: { timeout: number }) => number;
    cancelIdleCallback?: (handle: number) => void;
  };
  if (typeof idle.requestIdleCallback === "function") {
    const handle = idle.requestIdleCallback(run, { timeout: IDLE_TIMEOUT_MS });
    return { cancel: () => idle.cancelIdleCallback?.(handle) };
  }
  const handle = window.setTimeout(run, 32);
  return { cancel: () => window.clearTimeout(handle) };
}

interface Progress {
  thread: string | undefined;
  seeded: boolean;
  /** Key of the first mounted block; null when every block is mounted. */
  startKey: string | null;
  /** Key of the first block as of the last render: a new one in front means older turns were put there. */
  firstKey: string;
  /** Whether an idle slice has been taken for this conversation yet (the first one waits for the page to be quiet). */
  sliced: boolean;
}

/**
 * The index of the first block to mount. `keys` are the keys of all the blocks, oldest first; `scrollRef` is the
 * transcript's scroller, whose view is kept in place while blocks are added above it.
 *
 * `paused`: a turn is running. Building three blocks is a task of 100 ms or more, which in the middle of a stream is a
 * stutter in the one animation the reader is watching, so the older turns wait for the run to end (and then for the
 * page to be quiet again). The reader scrolling toward them is not paused: that is asked for.
 */
export function useProgressiveTurns(keys: readonly string[], threadId: string | undefined, scrollRef: RefObject<HTMLElement | null>, paused = false): number {
  const progress = useRef<Progress>({ thread: undefined, seeded: false, startKey: null, firstKey: "", sliced: false });
  const [, setRevision] = useState(0);
  const anchor = useRef<{ height: number } | null>(null);
  const keysRef = useRef(keys);
  keysRef.current = keys;

  const state = progress.current;
  if (state.thread !== threadId || (!state.seeded && keys.length > 0)) {
    // Entering a conversation (or the first blocks of it arriving): open with the newest.
    state.thread = threadId;
    state.seeded = keys.length > 0;
    state.startKey = openingStart(keys);
    state.firstKey = keys[0] ?? "";
    state.sliced = false;
  } else if (keys.length && state.firstKey !== keys[0]) {
    // Older turns were put in front of the ones on screen: the reader asked for them, so they appear at once.
    if (state.startKey !== null && keys.includes(state.firstKey)) state.startKey = null;
    state.firstKey = keys[0];
  }
  const start = mountedFrom(keys, state.startKey);

  /** Mount `step` more blocks. `keepView`: give back the height they add above a view that follows the live edge. */
  const advance = useRef((step: number, keepView: boolean) => {
    const scroller = scrollRef.current;
    const all = keysRef.current;
    const from = mountedFrom(all, progress.current.startKey);
    if (from <= 0) return;
    // A view that follows the live edge is the only thing that moves the page (scroll anchoring is off then), so the
    // height added above it has to be given back. A reader who scrolled away is kept in place by the browser.
    anchor.current = keepView && scroller && scroller.dataset.following === "true" ? { height: scroller.scrollHeight } : null;
    progress.current.startKey = steppedStart(all, from, step);
    setRevision((value) => value + 1);
  }).current;

  useLayoutEffect(() => {
    const scroller = scrollRef.current;
    const pending = anchor.current;
    if (pending) {
      anchor.current = null;
      if (scroller) {
        const added = scroller.scrollHeight - pending.height;
        if (added > 0) scroller.scrollTop += added;
      }
    }
    // A conversation of short turns does not fill the screen with the first blocks: mount more now, before anything
    // is painted, rather than show an empty top for the moment until the page is idle.
    if (start > 0 && scroller && scroller.scrollHeight < scroller.clientHeight * FILL_VIEWPORT_FACTOR) advance(FILL_STEP, false);
  }, [start, scrollRef, advance]);

  useEffect(() => {
    if (start <= 0) return undefined;
    if (paused) {
      // When the run ends the first slice waits for a quiet page again, as it does at opening.
      progress.current.sliced = false;
      return undefined;
    }
    let idle: Pending | null = null;
    const take = () => {
      idle = whenIdle(() => {
        progress.current.sliced = true;
        advance(IDLE_STEP, true);
      });
    };
    // The first slice of a conversation waits for the page to be quiet; the ones after it follow as the page allows.
    const wait = progress.current.sliced ? null : window.setTimeout(take, BACKFILL_DELAY_MS);
    if (wait === null) take();
    return () => {
      if (wait !== null) window.clearTimeout(wait);
      idle?.cancel();
    };
    // `keys` changes identity with every render; a slice only has to be scheduled again when what it works on changes.
  }, [start, keys.length, threadId, paused, advance]);

  useEffect(() => {
    const scroller = scrollRef.current;
    if (start <= 0 || !scroller) return undefined;
    let frame = 0;
    const onScroll = () => {
      // Only a reader who has left the live edge (the transcript's scroll policy says so before the scroll happens): the
      // scrolling the page does itself, pinning the bottom as it settles, is not a reason to build anything early.
      if (frame || scroller.dataset.following !== "false" || scroller.scrollTop > scroller.clientHeight * SCROLL_REACH_SCREENS) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        advance(SCROLL_STEP, true);
      });
    };
    scroller.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      scroller.removeEventListener("scroll", onScroll);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [start, threadId, scrollRef, advance]);

  return start;
}
