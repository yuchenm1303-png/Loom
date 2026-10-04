import type { TranscriptItem } from "../types/loom";

export interface TaskMilestone {
  step: string;
  status: "pending" | "in_progress" | "completed" | "blocked";
  evidence?: string;
  blocker?: string;
}

export function latestTaskPlan(items: TranscriptItem[]): TaskMilestone[] {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item.toolName !== "update_plan" || item.status !== "completed" || item.ok === false) continue;
    const result = item.result as { plan?: unknown } | undefined;
    if (!Array.isArray(result?.plan)) continue;
    return result.plan.filter((entry): entry is TaskMilestone => Boolean(entry)
      && typeof entry.step === "string"
      && ["pending", "in_progress", "completed", "blocked"].includes(entry.status));
  }
  return [];
}

/** Presentation only: preserve every item and every intervention, never infer finality. */
export function liveTaskProgress(items: TranscriptItem[], protectedIds = new Set<string>()) {
  let latest = -1;
  items.forEach((item, index) => {
    if (item.type === "assistant_message" && item.phase === "commentary") latest = index;
  });
  const earlier: TranscriptItem[] = [];
  const current: TranscriptItem[] = [];
  items.forEach((item, index) => {
    const intervention = protectedIds.has(item.id)
      || ["user_message", "approval", "error", "file_edit"].includes(item.type)
      || ["started", "running", "waiting", "streaming", "failed", "denied"].includes(item.status ?? "")
      || (item.type === "assistant_message" && item.phase !== "commentary");
    (index < latest && !intervention ? earlier : current).push(item);
  });
  return { earlier, current };
}
