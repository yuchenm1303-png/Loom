import type { ProgressFeedbackMode, TranscriptItem } from "../types/loom";

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
    if ((item.toolName === "update_plan" || (item.toolName === "manage_plan" && (item.arguments as { action?: string } | undefined)?.action !== "inspect")) && item.stepId) planSteps.add(String(item.stepId));
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
 * The reader's progress feedback level moves that last line: `detailed` shows every sentence, `quiet` drops
 * the length rule and keeps only the reply, plan reports and reactions to failures. `balanced` also shows a
 * sentence that breaks a silence: at that level the model is asked to speak only when a result changes the
 * picture, and those sentences are short (median 45-76 chars in replays), so the length rule alone hid them.
 * A model that narrates every step never breaks a silence, so its narration still stays in the log.
 * `released` names sentences that were already drawn because they waited out their hold; they stay.
 * Position, step status and length only, never the wording. Each verdict depends only on what came
 * before the sentence and on its own length, so it never changes once the sentence is drawn.
 */
export function reportIds(items: TranscriptItem[], released: ReadonlySet<string> = NO_IDS, mode: ProgressFeedbackMode = "balanced"): ReadonlySet<string> {
  const initial = initialUpdateIds(items);
  const plan = planUpdateNoteIds(items);
  const ids = new Set<string>();
  // A step failed since the model last said anything: its next sentence is the reaction.
  let failed = false;
  // Tool steps since the model last said anything. Its own step comes right after a sentence, so a
  // second one means a step went by in silence. Only calls that name their step count: without it,
  // parallel calls of one step would look like several silent steps.
  const steps = new Set<string>();
  for (const item of items) {
    if (item.type === "user_message") {
      failed = false;
      steps.clear();
    } else if (item.type === "tool_call" && !item.nested) {
      if (item.stepId) steps.add(item.stepId);
      if (isFailedStep(item)) failed = true;
    } else if (item.type === "assistant_message" && item.phase === "commentary") {
      const text = String(item.text ?? "").trim();
      if (!text) continue;
      const substantial = mode === "detailed"
        || (mode === "balanced" && (text.length >= SUBSTANTIAL_TEXT_CHARS || steps.size >= 2));
      if (initial.has(item.id) || plan.has(item.id) || released.has(item.id) || failed || substantial) ids.add(item.id);
      failed = false;
      steps.clear();
    }
  }
  return ids;
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

const thinkingOnly = new WeakMap<TranscriptItem, TranscriptItem>();

/**
 * The item as far as the reader may see it while its words wait: its reasoning (the model is thinking) and
 * nothing of the sentence that follows. Reasoning that arrives inside the text (a <think> block) is kept up to
 * where the block closes.
 */
function withoutWords(item: TranscriptItem): TranscriptItem {
  const text = String(item.text ?? "");
  const open = text.search(/<think>/i);
  const close = open < 0 ? -1 : text.toLowerCase().indexOf("</think>");
  const kept = open >= 0 && close >= 0 ? text.slice(0, close + "</think>".length) : open >= 0 ? text : "";
  if (kept === text) return item;
  let variant = thinkingOnly.get(item);
  if (!variant || variant.text !== kept) {
    variant = { ...item, text: kept };
    thinkingOnly.set(item, variant);
  }
  return variant;
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
 * Reasoning is the model thinking, not words about to be taken back: it stays on screen while its sentence
 * waits. Returns what to draw and the id of the held sentence.
 */
export function settleLiveText(items: TranscriptItem[], released: ReadonlySet<string> = NO_IDS, mode: ProgressFeedbackMode = "balanced"): { items: TranscriptItem[]; held: string } {
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
    if (!worked || !isUnclassified(item)) {
      settled.push(item);
      continue;
    }
    const probe = asCommentary(item);
    if (!reportIds(base.map((other) => (other === item ? probe : other)), released, mode).has(item.id)) {
      held = item.id;
      changed = true;
      if (showsReasoning(item)) settled.push(withoutWords(item));
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
    // Completed empty responses have no surface and must not split a batch or
    // create spacing slots. Live placeholders still own their thinking row.
    if (item.type === "assistant_message" && item.status !== "streaming"
      && !["running", "in_progress", "pending"].includes(item.status ?? "")
      && !String(item.text ?? "").trim() && !String(item.reasoning ?? "").trim()) continue;
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
