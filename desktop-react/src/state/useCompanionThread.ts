import { useCallback, useEffect, useRef, useState } from "react";
import type { LoomNotification } from "../types/global";
import type { ApprovalDecision, PendingApproval, ThreadReadResult, ThreadRecord, TranscriptItem, TurnRecord } from "../types/loom";
import { preservePendingUserIdentity, reconcilePendingUserMessage } from "../pendingUserMessage";
import { buildApprovalResponse } from "./approvalProtocol";
import { settleCompletedTurnItems, terminalStatus } from "./turnCompletion";
import { withThreadReadTimeout } from "./threadReadTimeout";

/**
 * The conversation in the split pane: a second, lighter copy of what useLoomCore keeps for the open conversation.
 *
 * It reads the last few turns, then follows that conversation's events on the same notification stream (the Host
 * broadcasts every conversation's), so the pane is live without polling. It can answer the conversation (a new turn,
 * or guidance while it runs), stop it and answer its approvals; model, permissions and attachments stay with the main
 * composer, where the conversation can be opened in full.
 */
const COMPANION_TURNS = 6;
const BUSY = new Set(["running", "waiting_approval"]);

export interface CompanionThread {
  thread: ThreadRecord | null;
  items: TranscriptItem[];
  running: boolean;
  loading: boolean;
  error: string;
  send(text: string): Promise<void>;
  interrupt(): Promise<void>;
  respondApproval(item: TranscriptItem, choice: ApprovalDecision | boolean, feedback?: string): Promise<void>;
}

function bridge(): Window["loom"] {
  const loom = Reflect.get(window, "loom") as Window["loom"] | null;
  if (!loom) throw new Error("Loom preload bridge is unavailable.");
  return loom;
}

function appendDelta(item: TranscriptItem, delta: Record<string, unknown>): TranscriptItem {
  const next: TranscriptItem = { ...item };
  for (const [key, value] of Object.entries(delta)) {
    if (["text", "reasoning", "stdout", "stderr"].includes(key) && typeof value === "string") {
      next[key] = `${String(next[key] ?? "")}${value}`;
    } else if (key === "arguments" && delta.kind === "tool_call_argument" && typeof value === "string") {
      next.arguments = `${typeof next.arguments === "string" ? next.arguments : ""}${value}`;
    } else {
      next[key] = value;
    }
  }
  return next;
}

function upsert(items: TranscriptItem[], incoming: TranscriptItem): TranscriptItem[] {
  const resolved = preservePendingUserIdentity(items, incoming);
  const current = reconcilePendingUserMessage(items, resolved);
  const index = current.findIndex(item => item.id === resolved.id);
  if (index < 0) return [...current, resolved];
  const next = current.slice();
  next[index] = { ...current[index], ...resolved };
  return next;
}

export function useCompanionThread(threadId: string): CompanionThread {
  const [thread, setThread] = useState<ThreadRecord | null>(null);
  const [items, setItems] = useState<TranscriptItem[]>([]);
  const [approval, setApproval] = useState<PendingApproval | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const threadRef = useRef<ThreadRecord | null>(null);
  threadRef.current = thread;
  // Deltas arrive per token: they are folded into the items once a frame.
  const deltas = useRef(new Map<string, Record<string, unknown>>());
  const frame = useRef(0);

  useEffect(() => {
    setThread(null);
    setItems([]);
    setApproval(null);
    setError("");
    if (!threadId) return;
    let disposed = false;
    // Events that arrive while the first read is in flight are replayed over it, so nothing between the read and the
    // subscription is lost.
    let buffered: LoomNotification[] | null = [];

    const flush = () => {
      frame.current = 0;
      if (!deltas.current.size) return;
      const pending = deltas.current;
      deltas.current = new Map();
      setItems(current => current.map(item => (pending.has(item.id) ? appendDelta(item, pending.get(item.id)!) : item)));
    };

    const apply = (message: LoomNotification) => {
      const params = (message.params ?? {}) as Record<string, unknown>;
      const item = params.item as TranscriptItem | undefined;
      const turn = params.turn as TurnRecord | undefined;
      const pending = params.approval as PendingApproval | undefined;
      const owner = String(params.threadId ?? item?.threadId ?? turn?.threadId ?? pending?.threadId
        ?? (params.thread as ThreadRecord | undefined)?.id ?? "");
      if (owner !== threadId) return;
      switch (message.method) {
        case "thread/updated": {
          const next = params.thread as ThreadRecord | undefined;
          if (next) setThread(current => ({ ...current, ...next }));
          return;
        }
        case "turn/started":
          if (turn) setThread(current => current && { ...current, status: "running", paused: false, currentTurnId: turn.id });
          return;
        case "turn/completed":
          if (!turn) return;
          flush();
          setApproval(null);
          setThread(current => current && (!current.currentTurnId || current.currentTurnId === turn.id)
            ? { ...current, status: turn.status, paused: false, currentTurnId: turn.id } : current);
          setItems(current => settleCompletedTurnItems(current, turn));
          return;
        case "turn/pauseState":
          setThread(current => current && current.currentTurnId === params.turnId ? { ...current, paused: Boolean(params.paused) } : current);
          return;
        case "approval/requested":
          if (pending) {
            setApproval(pending);
            setThread(current => current && { ...current, status: "waiting_approval", currentTurnId: pending.turnId });
          }
          return;
        case "item/started":
        case "item/completed":
          if (!item) return;
          if (message.method === "item/completed") {
            const queued = deltas.current.get(item.id);
            deltas.current.delete(item.id);
            if (queued) setItems(current => current.map(entry => entry.id === item.id ? appendDelta(entry, queued) : entry));
            if (item.type === "approval") setApproval(current => (current?.callId === item.callId ? null : current));
          }
          setItems(current => upsert(current, item));
          return;
        case "item/delta": {
          const id = String(params.itemId ?? "");
          const delta = params.delta as Record<string, unknown> | undefined;
          if (!id || !delta) return;
          const previous = deltas.current.get(id);
          const merged: Record<string, unknown> = previous ? { ...previous } : {};
          for (const [key, value] of Object.entries(delta)) {
            const appends = ["text", "reasoning", "stdout", "stderr"].includes(key)
              || (key === "arguments" && delta.kind === "tool_call_argument");
            merged[key] = appends && typeof value === "string" ? `${String(merged[key] ?? "")}${value}` : value;
          }
          deltas.current.set(id, merged);
          if (!frame.current) frame.current = window.requestAnimationFrame(flush);
          return;
        }
        case "thread/resync":
          void read();
          return;
        default:
      }
    };

    const read = async () => {
      setLoading(true);
      try {
        const result = await withThreadReadTimeout(bridge().call<ThreadReadResult>("thread/read", {
          threadId, presentationOnly: true, turnLimit: COMPANION_TURNS,
        }));
        if (disposed) return;
        const turns = result.turns ?? [];
        setThread(result.thread);
        setItems(turns.reduce(settleCompletedTurnItems, turns.flatMap(turn => turn.items ?? [])));
        setApproval(result.pendingApproval ?? null);
        setError("");
        const replay = buffered ?? [];
        buffered = null;
        replay.forEach(apply);
      } catch (cause) {
        if (!disposed) setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        if (!disposed) setLoading(false);
      }
    };

    const unsubscribe = bridge().onNotification(message => {
      if (disposed) return;
      if (buffered) buffered.push(message);
      else apply(message);
    });
    void read();
    return () => {
      disposed = true;
      unsubscribe();
      window.cancelAnimationFrame(frame.current);
      frame.current = 0;
      deltas.current.clear();
    };
  }, [threadId]);

  const send = useCallback(async (text: string) => {
    const input = text.trim();
    const current = threadRef.current;
    if (!input || !current) return;
    if (current.archived) throw new Error("该对话已归档，请先取消归档再发送。");
    if (BUSY.has(String(current.status)) && current.currentTurnId) {
      // While it runs, words become guidance for the turn in flight, as the main composer does.
      await bridge().call("turn/steer", {
        threadId: current.id, turnId: current.currentTurnId, input, attachments: [],
        clientInputId: globalThis.crypto?.randomUUID?.() ?? `steer-${Date.now()}`,
      });
      return;
    }
    const pendingId = `pending-user-${globalThis.crypto?.randomUUID?.() ?? Date.now()}`;
    setItems(list => [...list, {
      id: pendingId, clientMessageId: pendingId, threadId: current.id, type: "user_message",
      text: input, status: "sending", submittedAt: new Date().toISOString(),
    }]);
    try {
      const result = await bridge().call<{ turn: TurnRecord }>("turn/start", { threadId: current.id, input });
      const turn = result.turn;
      setItems(list => list.map(item => item.id === pendingId ? { ...item, turnId: turn.id } : item));
      setThread(latest => latest && !(latest.currentTurnId === turn.id && terminalStatus(latest.status))
        ? { ...latest, status: "running", paused: false, currentTurnId: turn.id } : latest);
    } catch (cause) {
      setItems(list => list.filter(item => item.id !== pendingId));
      throw cause;
    }
  }, []);

  const interrupt = useCallback(async () => {
    const current = threadRef.current;
    if (!current?.currentTurnId) return;
    await bridge().call("turn/interrupt", { threadId: current.id, turnId: current.currentTurnId });
  }, []);

  const respondApproval = useCallback(async (item: TranscriptItem, choice: ApprovalDecision | boolean, feedback?: string) => {
    if (!item.callId) return;
    await bridge().call("approval/respond", buildApprovalResponse(approval, item.callId, choice, feedback));
  }, [approval]);

  return {
    thread, items, loading, error, send, interrupt, respondApproval,
    running: BUSY.has(String(thread?.status ?? "")),
  };
}
