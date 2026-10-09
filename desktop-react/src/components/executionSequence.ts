import type { TranscriptItem } from "../types/loom";

export type TranscriptBlock =
  | { kind: "item"; item: TranscriptItem }
  | { kind: "activity"; items: TranscriptItem[] };

export function isActivityItem(item: TranscriptItem): boolean {
  return item.type === "tool_call" || item.type === "process" || item.type === "file_edit";
}

/** Commentary that may join a work log. Decisions and failed prose stay ordinary messages. */
export function isProcessCommentary(item: TranscriptItem, protectedIds: ReadonlySet<string> = new Set()): boolean {
  return item.type === "assistant_message" && item.phase === "commentary"
    && !protectedIds.has(item.id) && !["failed", "denied", "interrupted"].includes(item.status ?? "");
}

/**
 * The first commentary after each user message, before any tool work, answers that
 * person. It stays an ordinary message; every other commentary is log narration.
 * Position alone decides this, never the text.
 */
export function initialUpdateIds(items: TranscriptItem[]): ReadonlySet<string> {
  const ids = new Set<string>();
  let expecting = true;
  for (const item of items) {
    if (item.type === "user_message") {
      expecting = true;
    } else if (item.type === "assistant_message" && item.phase === "commentary") {
      if (expecting) ids.add(item.id);
      expecting = false;
    } else {
      expecting = false;
    }
  }
  return ids;
}

/**
 * Commentary sent in the same model step as a plan update. The model itself marks a stage
 * boundary there, so that sentence is its report for the stage. This reads the explicit
 * step identity of the plan call, never the wording of the message.
 */
export function planUpdateNoteIds(items: TranscriptItem[]): ReadonlySet<string> {
  const planSteps = new Set<string>();
  for (const item of items) {
    if (item.toolName === "update_plan" && item.stepId) planSteps.add(String(item.stepId));
  }
  const ids = new Set<string>();
  if (!planSteps.size) return ids;
  for (const item of items) {
    if (item.type === "assistant_message" && item.phase === "commentary" && item.stepId && planSteps.has(String(item.stepId))) ids.add(item.id);
  }
  return ids;
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
    const commentary = isProcessCommentary(item, protectedIds);
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
