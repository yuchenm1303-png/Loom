import { useLayoutEffect, useRef, useState } from "react";
import { useReducedMotion } from "../motion/useReducedMotion";
import type { TranscriptItem } from "../types/loom";

type Progress = { earlier: TranscriptItem[]; current: TranscriptItem[] };
type Snapshot = { progress: Progress; active: boolean; open: boolean; reduce: boolean;
  retained: Set<string>; folding: Set<string> };
const READ_HOLD_MS = 120;
const FOLD_LIFETIME_MS = 360; // CSS: 40ms pause + 300ms height contraction.

/** Retire already-painted records in place before transferring them to history. */
export function useEarlierProcessHandoff(items: TranscriptItem[], progress: Progress, active: boolean,
  open: boolean, pending: ReadonlySet<string>, groups: (items: TranscriptItem[]) => string[][]) {
  const reduce = useReducedMotion();
  const [state, setState] = useState<Snapshot>(() => ({ progress, active, open, reduce,
    retained: new Set(), folding: new Set() }));
  const timers = useRef(new Map<string, { phase: string; timer: number }>());
  let snapshot = state;
  if (progress !== state.progress || active !== state.active || open !== state.open || reduce !== state.reduce) {
    const earlierIds = new Set(progress.earlier.map(item => item.id));
    const retained = active && !reduce && !document.hidden
      ? new Set([...state.retained, ...(state.active ? state.progress.current.map(item => item.id) : [])]
        .filter(id => earlierIds.has(id))) : new Set<string>();
    snapshot = { progress, active, open, reduce, retained,
      folding: new Set(open ? [] : [...state.folding].filter(id => retained.has(id))) };
    // Adjust before reconciliation: an exiting record keeps its existing DOM,
    // tool disclosure and text paint snapshot, instead of unmounting for a frame.
    setState(snapshot);
  }
  const currentIds = new Set(progress.current.map(item => item.id));
  const current = items.filter(item => currentIds.has(item.id) || snapshot.retained.has(item.id));
  const earlier = progress.earlier.filter(item => !snapshot.retained.has(item.id));

  useLayoutEffect(() => {
    const eligible = new Set<string>();
    const batches = new Map<string, string[]>();
    if (!open) for (const ids of groups(current)) {
      // A tool group is one envelope. Never hide a live row together with its
      // completed neighbours, or fold text that is still draining its last burst.
      if (ids.every(id => snapshot.retained.has(id)
        && ![...pending].some(key => key.startsWith(`${id}:`)))) {
        ids.forEach(id => eligible.add(id));
        batches.set(JSON.stringify(ids), ids);
      }
    }
    const reversing = [...snapshot.folding].filter(id => !eligible.has(id));
    if (reversing.length) setState(previous => ({ ...previous,
      folding: new Set([...previous.folding].filter(id => !reversing.includes(id))) }));
    for (const [key, scheduled] of timers.current) {
      const ids = batches.get(key);
      const phase = ids?.every(id => snapshot.folding.has(id)) ? "folding" : "holding";
      if (!ids || scheduled.phase !== phase) {
        clearTimeout(scheduled.timer);
        timers.current.delete(key);
      }
    }
    for (const [key, ids] of batches) {
      if (timers.current.has(key)) continue;
      const phase = ids.every(id => snapshot.folding.has(id)) ? "folding" : "holding";
      const timer = window.setTimeout(() => {
        timers.current.delete(key);
        setState(previous => {
          if (!previous.active || previous.open || !ids.every(id => previous.retained.has(id))) return previous;
          const retained = new Set(previous.retained);
          const folding = new Set(previous.folding);
          for (const id of ids) {
            if (phase === "holding") folding.add(id);
            else { retained.delete(id); folding.delete(id); }
          }
          return { ...previous, retained, folding };
        });
      }, phase === "holding" ? READ_HOLD_MS : FOLD_LIFETIME_MS);
      timers.current.set(key, { phase, timer });
    }
  }, [current, groups, open, pending, snapshot]);

  useLayoutEffect(() => {
    const settleHidden = () => {
      if (document.hidden) setState(previous => ({ ...previous, retained: new Set(), folding: new Set() }));
    };
    document.addEventListener("visibilitychange", settleHidden);
    return () => {
      document.removeEventListener("visibilitychange", settleHidden);
      timers.current.forEach(({ timer }) => clearTimeout(timer));
      timers.current.clear();
    };
  }, []);
  return { current, earlier, retained: snapshot.retained, folding: snapshot.folding };
}
