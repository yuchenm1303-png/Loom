import { useEffect, useMemo, useRef, useState } from "react";
import type { TranscriptItem } from "../types/loom";

const leavingVariants = new WeakMap<TranscriptItem, TranscriptItem>();

function asLeaving(item: TranscriptItem): TranscriptItem {
  let variant = leavingVariants.get(item);
  if (!variant) {
    variant = { ...item, leaving: true };
    leavingVariants.set(item, variant);
  }
  return variant;
}

/**
 * A transcript item that was on screen and no longer should be (an approval the reader just answered) is not
 * removed in the frame it resolves: it stays, marked `leaving`, for `ms` so the view can fold it away instead
 * of taking its height back in one frame. Only items `track` selects are looked after; everything else passes
 * through. With `ms` of 0 (reduced motion) this is a plain filter.
 */
export function useLeavingItems(
  items: TranscriptItem[],
  shown: (item: TranscriptItem) => boolean,
  track: (item: TranscriptItem) => boolean,
  ms: number,
  dependencies: readonly unknown[] = [],
): TranscriptItem[] {
  const drawn = useRef(new Set<string>());
  const leavingUntil = useRef(new Map<string, number>());
  const [tick, setTick] = useState(0);

  const result = useMemo(() => {
    const now = performance.now();
    if (ms > 0) {
      for (const item of items) {
        if (track(item) && !shown(item) && drawn.current.has(item.id) && !leavingUntil.current.has(item.id)) {
          leavingUntil.current.set(item.id, now + ms);
        }
      }
    }
    for (const [id, until] of leavingUntil.current) {
      if (now >= until || ms <= 0) leavingUntil.current.delete(id);
    }
    const next: TranscriptItem[] = [];
    const nowDrawn = new Set<string>();
    for (const item of items) {
      if (!track(item)) next.push(item);
      else if (shown(item)) {
        nowDrawn.add(item.id);
        next.push(item);
      } else if (leavingUntil.current.has(item.id)) next.push(asLeaving(item));
    }
    drawn.current = nowDrawn;
    return next;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, ms, tick, ...dependencies]);

  useEffect(() => {
    let due = Infinity;
    for (const until of leavingUntil.current.values()) due = Math.min(due, until);
    if (due === Infinity) return;
    const timer = window.setTimeout(() => setTick((value) => value + 1), Math.max(0, due - performance.now()) + 8);
    return () => window.clearTimeout(timer);
  }, [result]);

  return result;
}
