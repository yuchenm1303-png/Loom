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

/** A sentence this long, streaming after tool work, is taken to be the answer on its way. */
export const LIVE_TEXT_REVEAL_CHARS = 160;

const commentaryVariants = new WeakMap<TranscriptItem, TranscriptItem>();

function asCommentary(item: TranscriptItem): TranscriptItem {
  let variant = commentaryVariants.get(item);
  if (!variant) {
    variant = { ...item, phase: "commentary" };
    commentaryVariants.set(item, variant);
  }
  return variant;
}

function isReadableAnswer(item: TranscriptItem): boolean {
  const text = String(item.text ?? "");
  return Boolean(String(item.reasoning ?? "").trim()) || /<think/i.test(text) || text.trim().length >= LIVE_TEXT_REVEAL_CHARS;
}

/**
 * The runtime gives a step's sentence its phase only when the response completes, so while it
 * streams it has none, and drawing it as a message makes narration pop up and then vanish.
 * The list itself already says most of what is needed:
 * - a tool call streaming after the sentence, in the same step, makes it narration;
 * - the first sentence after a user message, before any tool work, answers that message;
 * - any other short sentence after tool work is narration or the answer on its way, and nobody
 *   knows yet: it is held back until it is long enough to be an answer, or `released` names it
 *   because the hold ran out. Reasoning shown while it streams is never held.
 * Position and length only, never the wording. Returns what to draw and the id of the held sentence.
 */
export function settleLiveText(items: TranscriptItem[], released = ""): { items: TranscriptItem[]; held: string } {
  let lastWork = -1;
  items.forEach((item, index) => { if (isActivityItem(item)) lastWork = index; });
  let worked = false;
  let changed = false;
  let held = "";
  const settled: TranscriptItem[] = [];
  items.forEach((item, index) => {
    if (item.type === "user_message") worked = false;
    else if (isActivityItem(item)) worked = true;
    const unclassified = item.type === "assistant_message" && item.status === "streaming" && !item.phase;
    if (!unclassified) settled.push(item);
    else if (lastWork > index) {
      settled.push(asCommentary(item));
      changed = true;
    } else if (worked && item.id !== released && !isReadableAnswer(item)) {
      held = item.id;
      changed = true;
    } else settled.push(item);
  });
  return { items: changed ? settled : items, held };
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
