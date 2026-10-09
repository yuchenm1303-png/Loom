/**
 * A long tool chain reads as noise and costs one mounted row per call. Consecutive
 * steps of the same kind collapse into one summary line; their rows mount only
 * when it is opened. This only decides what is grouped, never what happened.
 */
export interface Run<T> {
  /** What the entries have in common, e.g. a tool category. */
  key: string;
  /** Stable identity: the first entry's id, so a growing run keeps its React key. */
  id: string;
  entries: T[];
}

export function runsOf<T>(entries: readonly T[], keyOf: (entry: T) => string, idOf: (entry: T) => string): Run<T>[] {
  const runs: Run<T>[] = [];
  for (const entry of entries) {
    const key = keyOf(entry);
    const last = runs[runs.length - 1];
    if (last && last.key === key) last.entries.push(entry);
    else runs.push({ key, id: idOf(entry), entries: [entry] });
  }
  return runs;
}

/** One or two steps stay as plain rows; from this many on they collapse to a summary line. */
export const CLUSTER_MIN_ROWS = 3;
