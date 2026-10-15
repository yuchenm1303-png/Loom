import type { ThreadRecord } from "../types/loom";

/** Stable parent-first order, with orphaned and filtered children still navigable. */
export function orderThreadBranches(rows: ThreadRecord[]): ThreadRecord[] {
  const ids = new Set(rows.map((thread) => thread.id));
  const children = new Map<string, ThreadRecord[]>();
  const roots: ThreadRecord[] = [];
  for (const thread of rows) {
    const parent = thread.parentThreadId;
    if (parent && parent !== thread.id && ids.has(parent)) {
      const existing = children.get(parent) || [];
      existing.push(thread);
      children.set(parent, existing);
    } else {
      roots.push(thread);
    }
  }
  const result: ThreadRecord[] = [];
  const visited = new Set<string>();
  const visit = (thread: ThreadRecord) => {
    if (visited.has(thread.id)) return;
    visited.add(thread.id);
    result.push(thread);
    for (const child of children.get(thread.id) || []) visit(child);
  };
  roots.forEach(visit);
  // Corrupt/cyclic metadata must not make conversations disappear.
  rows.forEach(visit);
  return result;
}

export function branchDepth(thread: ThreadRecord, rows: ThreadRecord[]): number {
  const lookup = new Map(rows.map((item) => [item.id, item]));
  let depth = 0;
  let current = thread;
  const seen = new Set([thread.id]);
  while (current.parentThreadId && lookup.has(current.parentThreadId) && depth < 5) {
    if (seen.has(current.parentThreadId)) break;
    seen.add(current.parentThreadId);
    depth += 1;
    current = lookup.get(current.parentThreadId)!;
  }
  return depth;
}
