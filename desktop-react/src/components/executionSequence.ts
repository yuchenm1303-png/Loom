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

/** A sentence this long says enough to be a message to the reader, wherever it comes. */
export const SUBSTANTIAL_TEXT_CHARS = 160;

const NO_IDS: ReadonlySet<string> = new Set();

/** A tool step that went wrong: a failed command, a denied call. */
const isFailedStep = (item: TranscriptItem): boolean =>
  item.type === "tool_call" && !item.nested && ["failed", "denied"].includes(String(item.status ?? ""));

/**
 * The model's sentences that are messages to the reader. Most models introduce nearly every tool
 * call, and a log of that is noise; but an agent that says nothing after its first sentence leaves
 * the reader guessing. A person speaks when something happens, not on a timer, so narration stays
 * in the log except for:
 * - the reply to the user (the first sentence after a user message, before any tool work);
 * - the sentence sent with a plan update, which is the model's own stage report;
 * - the sentence that answers a failed step: the model's reading of what went wrong and what it
 *   will do instead, which is what the reader wants to know when the log turns red;
 * - any sentence long enough to carry real content.
 * `released` names sentences that were already drawn because they waited out their hold; they stay.
 * Position, step status and length only, never the wording. Each verdict depends only on what came
 * before the sentence and on its own length, so it never changes once the sentence is drawn.
 */
export function reportIds(items: TranscriptItem[], released: ReadonlySet<string> = NO_IDS): ReadonlySet<string> {
  const initial = initialUpdateIds(items);
  const plan = planUpdateNoteIds(items);
  const ids = new Set<string>();
  // A step failed since the model last said anything: its next sentence is the reaction.
  let failed = false;
  for (const item of items) {
    if (item.type === "user_message") failed = false;
    else if (isFailedStep(item)) failed = true;
    else if (item.type === "assistant_message" && item.phase === "commentary") {
      const text = String(item.text ?? "").trim();
      if (!text) continue;
      if (initial.has(item.id) || plan.has(item.id) || released.has(item.id) || failed || text.length >= SUBSTANTIAL_TEXT_CHARS) ids.add(item.id);
      failed = false;
    }
  }
  return ids;
}

/**
 * The model's messages to the reader, in order: everything it said that is not log narration. When a
 * finished turn folds its work log these stay on screen, as they do in a transcript, so only the tool
 * detail goes. Blank text and hidden narration are not messages.
 */
export function bodyMessages(items: TranscriptItem[], messageIds: ReadonlySet<string>, protectedIds: ReadonlySet<string>): TranscriptItem[] {
  return items.filter((item) => item.type === "assistant_message"
    && (messageIds.has(item.id) || !isProcessCommentary(item, protectedIds))
    && String(item.text ?? "").trim().length > 0);
}

const commentaryVariants = new WeakMap<TranscriptItem, TranscriptItem>();

function asCommentary(item: TranscriptItem): TranscriptItem {
  let variant = commentaryVariants.get(item);
  if (!variant) {
    variant = { ...item, phase: "commentary" };
    commentaryVariants.set(item, variant);
  }
  return variant;
}

const isUnclassified = (item: TranscriptItem): boolean => item.type === "assistant_message" && item.status === "streaming" && !item.phase;

function showsReasoning(item: TranscriptItem): boolean {
  return Boolean(String(item.reasoning ?? "").trim()) || /<think/i.test(String(item.text ?? ""));
}

/**
 * The runtime gives a step's sentence its phase only when the response completes, so while it
 * streams it has none, and drawing it as a message makes narration pop up and then vanish.
 * The list itself already says most of what is needed:
 * - a tool call streaming after the sentence, in the same step, makes it narration;
 * - the first sentence after a user message, before any tool work, answers that message;
 * - after tool work, any other sentence is narration or the answer on its way and nobody knows
 *   yet. If it would be a message as narration (see reportIds) it is drawn now and stays; if not,
 *   it is held back until it is long enough, or `released` names it because the hold ran out.
 * Reasoning shown while it streams is never held. Returns what to draw and the id of the held sentence.
 */
export function settleLiveText(items: TranscriptItem[], released: ReadonlySet<string> = NO_IDS): { items: TranscriptItem[]; held: string } {
  let lastWork = -1;
  items.forEach((item, index) => { if (isActivityItem(item)) lastWork = index; });
  let changed = false;
  const base = items.map((item, index) => {
    if (!isUnclassified(item) || lastWork <= index) return item;
    changed = true;
    return asCommentary(item);
  });
  let held = "";
  let worked = false;
  const settled: TranscriptItem[] = [];
  for (const item of base) {
    if (item.type === "user_message") worked = false;
    else if (isActivityItem(item)) worked = true;
    if (!worked || !isUnclassified(item) || showsReasoning(item)) {
      settled.push(item);
      continue;
    }
    const probe = asCommentary(item);
    if (!reportIds(base.map((other) => (other === item ? probe : other)), released).has(item.id)) {
      held = item.id;
      changed = true;
    } else if (String(item.text ?? "").trim().length < SUBSTANTIAL_TEXT_CHARS) {
      // Drawn at once. A short one answers a failed step or waited out its hold: it is narration, so it
      // sits in the work log from its first word and does not move when its tool call arrives.
      settled.push(probe);
      changed = true;
    } else settled.push(item); // A long one may be the answer itself: an ordinary message until its step says otherwise.
  }
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
