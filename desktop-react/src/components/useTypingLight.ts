import { useLayoutEffect, useRef, type RefObject } from "react";
import { useReducedMotion } from "../motion/useReducedMotion";
import { streamingGraphemes } from "./streamingText";

/**
 * The typing edge: the newest letters of a streaming answer glow, and cool in steps as newer ones arrive.
 *
 * It is painted with the CSS Custom Highlight API (::highlight(loom-type-N) in generation-motion.css): ranges over
 * the last text node, three bands of graphemes, no element in the text. The text used to be rewritten around its
 * last six letters (a span that faded in once), which made the rendered tree of a message differ while it streamed
 * and when it was complete, and cost a style invalidation a frame for an effect nobody could see. A highlight is
 * only a paint instruction, so the message is the same DOM before, during and after.
 *
 * Nothing animates by itself: each arrival re-aims the ranges (a layout effect, so the new letters are never painted
 * before their glow), and a pause lets the bands go out one after another, newest first.
 */

interface Band {
  name: string;
  /** Graphemes counted back from the end of the text where the band starts and ends. */
  from: number;
  to: number;
  /** How long after the last arrival the band goes out. */
  coolsAfterMs: number;
}

const BANDS: readonly Band[] = [
  { name: "loom-type-0", from: 0, to: 2, coolsAfterMs: 150 },
  { name: "loom-type-1", from: 2, to: 5, coolsAfterMs: 300 },
  { name: "loom-type-2", from: 5, to: 9, coolsAfterMs: 450 },
];
const REACH = BANDS[BANDS.length - 1].to;

function registry(): HighlightRegistry | null {
  return typeof CSS !== "undefined" && "highlights" in CSS && typeof Highlight !== "undefined" ? CSS.highlights : null;
}

/** The last text with something to read in it, found from the end so it costs the depth of the tree, not its size. */
function lastText(node: Node): Text | null {
  for (let child = node.lastChild; child; child = child.previousSibling) {
    if (child.nodeType === Node.TEXT_NODE) {
      if ((child as Text).data.trim()) return child as Text;
    } else if (child.nodeType === Node.ELEMENT_NODE) {
      const found = lastText(child);
      if (found) return found;
    }
  }
  return null;
}

// Only one message streams at a time, and the registry is global: the one that lit it is the one that may put it out.
let owner: object | null = null;

/**
 * @param active  the message is still being written (arriving or being typed out)
 * @param version changes whenever the visible text does
 */
export function useTypingLight(root: RefObject<HTMLElement | null>, active: boolean, version: string): void {
  const reduce = useReducedMotion();
  const token = useRef({}).current;
  const timers = useRef<number[]>([]);

  useLayoutEffect(() => {
    const highlights = registry();
    const element = root.current;
    if (!highlights || !element || reduce) return;
    for (const id of timers.current) window.clearTimeout(id);
    timers.current = [];

    const text = active ? lastText(element) : null;
    if (text) {
      owner = token;
      const tail = text.data.slice(-REACH * 8);
      const graphemes = streamingGraphemes(tail);
      // Code units in the last n graphemes, for n = 0..REACH.
      const reach: number[] = [0];
      for (let count = 1; count <= REACH && count <= graphemes.length; count += 1) {
        reach.push(reach[count - 1] + graphemes[graphemes.length - count].length);
      }
      for (const band of BANDS) {
        if (band.from >= reach.length - 1) {
          highlights.delete(band.name);
          continue;
        }
        const range = new Range();
        range.setStart(text, text.data.length - reach[Math.min(band.to, reach.length - 1)]);
        range.setEnd(text, text.data.length - reach[band.from]);
        highlights.set(band.name, new Highlight(range));
      }
    }
    if (owner !== token) return;
    // The bands go out in order once nothing new has come.
    for (const band of BANDS) {
      timers.current.push(window.setTimeout(() => {
        if (owner === token) highlights.delete(band.name);
      }, band.coolsAfterMs));
    }
  }, [root, active, version, reduce, token]);

  useLayoutEffect(() => () => {
    for (const id of timers.current) window.clearTimeout(id);
    timers.current = [];
    if (owner !== token) return;
    owner = null;
    const highlights = registry();
    for (const band of BANDS) highlights?.delete(band.name);
  }, [token]);
}
