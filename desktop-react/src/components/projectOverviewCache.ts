/* The last overview of each conversation, outside the hub's lazily loaded chunk so the header's switch can read ahead:
 * a hub whose overview is known draws at once, and the shell of the mode switch (modeMorph.ts) lands on it. */
import type { ProjectOverview } from "./projectOverview";

export const lastOverview = new Map<string, ProjectOverview>();
const reading = new Set<string>();

/** Reads a conversation's overview ahead of its hub, unless it is known or already being read. Opportunistic: the hub reads again when it mounts. */
export function readOverviewAhead(threadId: string) {
  if (lastOverview.has(threadId) || reading.has(threadId) || !window.loom?.call) return;
  reading.add(threadId);
  void window.loom.call<ProjectOverview>("projectRun/overview", { threadId })
    .then((result) => { lastOverview.set(threadId, result); }, () => {})
    .finally(() => reading.delete(threadId));
}
