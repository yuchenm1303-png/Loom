import type { LoomBridge } from "../types/global";

export interface ThreadHandoff {
  id: string; sourceThreadId: string; threadId: string; title: string; summary: string;
  display: "window" | "split" | "current" | "background";
  state: "waiting" | "starting" | "running" | "approval" | "completed" | "failed" | "interrupted";
  error?: string; createdAt: string; updatedAt?: string; sourcePaused?: boolean; turnId: string; created?: boolean;
  packet?: { task: string; summary: string; completed: string[]; nextSteps: string[]; evidence: string[] };
}
export interface HandoffStatus { incoming: ThreadHandoff | null; outgoing: ThreadHandoff[] }
export interface HandoffNavigation { current(id: string): Promise<unknown> | void; split?(id: string): Promise<unknown> | void }
export function conversationFromHash(hash: string): string | null {
  const id = new URLSearchParams(hash.replace(/^#/, "")).get("thread");
  return id && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(id) ? id : null;
}

export async function openHandoff(record: ThreadHandoff, navigation: HandoffNavigation, bridge: Pick<LoomBridge, "openThreadWindow">,
  display = record.display): Promise<ThreadHandoff["display"]> {
  if (!record.threadId || record.threadId === record.sourceThreadId) throw new Error("Invalid handoff destination");
  if (display === "background") return "background";
  if (display === "window" && bridge.openThreadWindow) {
    if (!await bridge.openThreadWindow(record.threadId)) throw new Error("The conversation was created but its window could not open");
    return "window";
  }
  if ((display === "split" || display === "window") && navigation.split) { await navigation.split(record.threadId); return "split"; }
  await navigation.current(record.threadId);
  return "current";
}
