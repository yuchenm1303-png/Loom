import { createContext, useContext, useLayoutEffect, useState } from "react";
import type { FileEditDelta } from "./activityModel";

/** True inside the live sequence of an active turn (not its earlier history). */
export const LiveSequenceContext = createContext(false);

/** The model's per-step narration stays out of the log unless the reader asks for it, once per turn. */
export const ProcessNotesContext = createContext<{ show: boolean }>({ show: false });

/** Turn-wide view of each diff snapshot, so a row shows only its own files. */
export const FileEditDeltaContext = createContext<ReadonlyMap<string, FileEditDelta>>(new Map());

/**
 * Activity rows and stages the renderer has already shown. One-shot motion is
 * bound to a row's first appearance in a live turn, never to a class that can
 * toggle later (a stage regaining its running state used to replay every
 * row's birth), and never to history or a remount after switching threads.
 */
const seenActivity = new Set<string>();

/**
 * `liveOverride` is for what sits outside the live sequence's provider but belongs to the same live turn
 * (the switch for the model's notes, which is the turn's, not a step's).
 */
export function useBornLive(key: string, liveOverride?: boolean): boolean {
  const contextLive = useContext(LiveSequenceContext);
  const live = liveOverride ?? contextLive;
  const [born] = useState(() => live && !seenActivity.has(key));
  useLayoutEffect(() => {
    seenActivity.add(key);
  }, [key]);
  return born;
}
