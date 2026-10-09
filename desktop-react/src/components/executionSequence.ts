import type { TranscriptItem } from "../types/loom";

export type TranscriptBlock =
  | { kind: "item"; item: TranscriptItem }
  | { kind: "activity"; items: TranscriptItem[] };

export function isActivityItem(item: TranscriptItem): boolean {
  return item.type === "tool_call" || item.type === "process" || item.type === "file_edit";
}

/** Explicit phases only: never infer importance or finality from message text. */
export function groupExecutionSequence(items: TranscriptItem[], protectedIds: ReadonlySet<string> = new Set()): TranscriptBlock[] {
  const blocks: TranscriptBlock[] = [];
  let activity: TranscriptItem[] = [];
  let turn = "";
  const flush = () => {
    if (activity.length) blocks.push({ kind: "activity", items: activity });
    activity = [];
    turn = "";
  };
  for (const item of items) {
    const nextTurn = String(item.turnId ?? "");
    if (activity.length && nextTurn !== turn) flush();
    const commentary = item.type === "assistant_message" && item.phase === "commentary"
      && !protectedIds.has(item.id) && !["failed", "denied", "interrupted"].includes(item.status ?? "");
    // Keep the initial update outside the execution disclosure. Subsequent
    // commentary belongs to the same ordered run, including streaming text.
    if (isActivityItem(item) || (commentary && activity.length)) {
      if (!activity.length) turn = nextTurn;
      activity.push(item);
    } else {
      flush();
      blocks.push({ kind: "item", item });
    }
  }
  flush();
  return blocks;
}
