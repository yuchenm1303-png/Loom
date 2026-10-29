import { useSyncExternalStore } from "react";
import type { LoomNotification } from "../types/global";
import type { ThreadRecord, TranscriptItem } from "../types/loom";

/**
 * What every conversation is doing, read from the Host's notification stream.
 *
 * The Host broadcasts every conversation's turn and item events; the main state (useLoomCore) keeps only the open
 * conversation's. This store keeps one small record per conversation (when its turn started, how many steps it took,
 * its latest step, how it ended) so the run panel can say what the others are doing without opening them. Item deltas
 * are ignored: a step's start and end are enough to say what is happening, and deltas are most of the traffic.
 */
export interface ConversationActivity {
  threadId: string;
  turnId: string;
  /** When the current (or last) turn started, ms since epoch; 0 when it started before Loom was watching. */
  startedAt: number;
  /** Tool steps seen in this turn. */
  steps: number;
  /** The latest step (a tool call, a process or a file edit) or reply, as the Host sent it. */
  latest: TranscriptItem | null;
  /** The model is writing its answer (the latest item is a final reply that has not completed). */
  replying: boolean;
  endedAt: number;
  endStatus: string;
  /** The reader has looked at the conversation since its turn ended (or dismissed it from the panel). */
  seen: boolean;
}

type Snapshot = ReadonlyMap<string, ConversationActivity>;

const STEP_TYPES = new Set(["tool_call", "process", "file_edit", "approval"]);
const TERMINAL = new Set(["completed", "failed", "cancelled", "interrupted", "error", "stopped"]);

let records = new Map<string, ConversationActivity>();
let snapshot: Snapshot = records;
let attention = 0;
const listeners = new Set<() => void>();
let unsubscribe: (() => void) | null = null;
let frame = 0;
/** The conversation on screen: its turns end in front of the reader, so they are seen as they end. */
let watchedIds = new Set<string>();

function publish() {
  if (frame) return;
  // Several items can land in one task (a batch of tool calls); one render answers them all.
  frame = window.requestAnimationFrame(() => {
    frame = 0;
    snapshot = new Map(records);
    attention = [...records.values()].filter(record => record.endedAt && !record.seen).length;
    listeners.forEach(listener => listener());
  });
}

function blank(threadId: string): ConversationActivity {
  return { threadId, turnId: "", startedAt: 0, steps: 0, latest: null, replying: false, endedAt: 0, endStatus: "", seen: true };
}

function update(threadId: string, change: (record: ConversationActivity) => ConversationActivity | null) {
  if (!threadId) return;
  const next = change(records.get(threadId) ?? blank(threadId));
  if (!next) return;
  records.set(threadId, next);
  publish();
}

function stamp(value: unknown): number {
  const parsed = Date.parse(String(value ?? ""));
  return Number.isFinite(parsed) ? parsed : 0;
}

/** `startedAt` 0 = not known (a turn first seen mid-way); the panel's seed read fills it in. */
function begin(record: ConversationActivity, turnId: string, startedAt: number): ConversationActivity {
  if (record.turnId === turnId && !record.endedAt) return record;
  return { ...blank(record.threadId), turnId, startedAt, seen: true };
}

function end(record: ConversationActivity, status: string): ConversationActivity {
  if (record.endedAt) return record.endStatus === status ? record : { ...record, endStatus: status };
  return { ...record, replying: false, endedAt: Date.now(), endStatus: status, seen: watchedIds.has(record.threadId) };
}

function onNotification(message: LoomNotification) {
  const params = (message.params ?? {}) as Record<string, unknown>;
  const item = params.item as TranscriptItem | undefined;
  const turn = params.turn as { id?: string; status?: string; startedAt?: string } | undefined;
  const approval = params.approval as { threadId?: string; turnId?: string } | undefined;
  const threadId = String(params.threadId ?? item?.threadId ?? (turn as { threadId?: string } | undefined)?.threadId ?? approval?.threadId ?? "");
  switch (message.method) {
    case "turn/started":
      if (turn?.id) update(threadId, record => begin(record, String(turn.id), stamp(turn.startedAt) || Date.now()));
      return;
    case "turn/completed":
      if (turn) update(threadId, record => (!record.turnId || record.turnId === turn.id
        ? end({ ...record, turnId: String(turn.id ?? record.turnId) }, String(turn.status || "completed")) : null));
      return;
    case "item/started":
    case "item/completed": {
      if (!item) return;
      const step = STEP_TYPES.has(item.type);
      const reply = item.type === "assistant_message" && item.phase !== "commentary";
      if (!step && !reply) return;
      update(threadId, record => {
        // An item of a turn Loom did not see start (it was running before the window opened) starts the record.
        const live = item.turnId && record.turnId !== item.turnId ? begin(record, String(item.turnId), 0) : record;
        if (live.endedAt) return null;
        const started = message.method === "item/started";
        if (reply) return { ...live, replying: started || item.status === "streaming" };
        const sameStep = live.latest?.id === item.id;
        // A completion only replaces the step on show when it is that step (an earlier one finishing says nothing new).
        if (!started && live.latest && !sameStep) return null;
        return { ...live, latest: item, replying: false, steps: live.steps + (started && !sameStep ? 1 : 0) };
      });
      return;
    }
    case "thread/updated": {
      const thread = params.thread as ThreadRecord | undefined;
      if (!thread?.id) return;
      const status = String(thread.status ?? "");
      if (TERMINAL.has(status)) {
        update(thread.id, record => (record.turnId && !record.endedAt
          && (!thread.currentTurnId || thread.currentTurnId === record.turnId) ? end(record, status) : null));
      }
      return;
    }
    case "thread/deleted":
      if (records.delete(threadId)) publish();
      return;
    default:
  }
}

/** Starts listening once; safe to call on every render of the app. */
export function startConversationActivity(): void {
  if (unsubscribe) return;
  const bridge = Reflect.get(window, "loom") as Window["loom"] | null;
  if (!bridge?.onNotification) return;
  unsubscribe = bridge.onNotification(onNotification);
}

/**
 * The conversations on screen (the open one and the one in the split pane). Their turns end in front of the reader,
 * and opening a conversation marks what it finished as seen.
 */
export function watchConversations(ids: readonly string[]): void {
  watchedIds = new Set(ids.filter(Boolean));
  let changed = false;
  watchedIds.forEach(id => {
    const record = records.get(id);
    if (record && !record.seen) { records.set(id, { ...record, seen: true }); changed = true; }
  });
  if (changed) publish();
}

/** Clears the "finished" mark of one conversation, or of every conversation. */
export function dismissConversationActivity(threadId?: string): void {
  let changed = false;
  records.forEach((record, id) => {
    if ((!threadId || id === threadId) && record.endedAt && !record.seen) {
      records.set(id, { ...record, seen: true });
      changed = true;
    }
  });
  if (changed) publish();
}

/**
 * A turn that was already running when Loom started has no start time or step: the panel asks the Host once for its
 * last turn and seeds the record from it.
 */
export function seedConversationActivity(threadId: string, turn: { id?: string; startedAt?: string; items?: TranscriptItem[] } | undefined): void {
  if (!turn?.id) return;
  update(threadId, record => {
    if (record.turnId && record.turnId !== turn.id) return null;
    if (record.startedAt && record.latest) return null;
    const items = turn.items ?? [];
    const steps = items.filter(item => STEP_TYPES.has(item.type));
    // Steps counted live since Loom started watching are the newest of the turn; the read knows the ones before.
    const known = new Set(steps.map(item => item.id));
    return {
      ...record,
      turnId: String(turn.id),
      startedAt: record.startedAt || stamp(turn.startedAt),
      steps: known.size + (record.latest && !known.has(record.latest.id) ? record.steps : 0),
      latest: record.latest ?? steps.at(-1) ?? null,
    };
  });
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function useConversationActivity(): Snapshot {
  return useSyncExternalStore(subscribe, () => snapshot, () => snapshot);
}

/** How many conversations finished out of sight and have not been looked at: the dock's dot. */
export function useConversationAttention(): number {
  return useSyncExternalStore(subscribe, () => attention, () => attention);
}

/** Test seam: forget everything and stop listening. */
export function resetConversationActivity(): void {
  unsubscribe?.();
  unsubscribe = null;
  records = new Map();
  snapshot = records;
  attention = 0;
  watchedIds = new Set();
}
