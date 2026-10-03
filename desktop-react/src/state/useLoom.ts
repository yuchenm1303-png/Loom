import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ThreadRecord, TranscriptItem } from "../types/loom";
import { useLoom as useLoomCore } from "./useLoomCore";

function threadIsRunning(thread?: ThreadRecord | null): boolean {
  return thread?.status === "running" || thread?.status === "waiting_approval";
}

function currentTurnHasTerminalError(items: TranscriptItem[], currentTurnId?: string | null): boolean {
  const targetTurnId = String(currentTurnId ?? "").trim();
  if (!targetTurnId) return false;
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (String(item.turnId ?? "").trim() !== targetTurnId || item.type !== "error") continue;
    const status = String(item.status ?? "").toLowerCase();
    return !["started", "running", "streaming", "pending", "waiting"].includes(status);
  }
  return false;
}

function steeringInputId(): string {
  const cryptoApi = globalThis.crypto as Crypto & { randomUUID?: () => string };
  return cryptoApi?.randomUUID?.()
    ?? `steer-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

type SteeringReceipt = {
  inputId?: string;
  submittedAt?: string | null;
  displayText?: string | null;
  attachments?: Array<{ name?: string; path?: string; kind?: string }>;
};

function optimisticStartItem(
  threadId: string,
  inputId: string,
  text: string,
  attachments: { path: string; name: string }[],
  submittedAt: string,
): TranscriptItem {
  const displayText = text || (attachments.length === 1
    ? attachments[0].name
    : attachments.length
      ? `${attachments.length} attachments`
      : "");
  return {
    id: `optimistic-start-${inputId}`,
    threadId,
    turnId: null,
    type: "user_message",
    status: "pending",
    text: displayText,
    source: "user",
    inputId,
    submittedAt,
    createdAt: submittedAt,
    optimistic: true,
  };
}

function optimisticSteeringItem(
  threadId: string,
  turnId: string,
  inputId: string,
  text: string,
  submittedAt: string,
): TranscriptItem {
  return {
    id: `optimistic-steer-${inputId}`,
    threadId,
    turnId,
    type: "user_message",
    status: "pending",
    text,
    source: "steering",
    inputId,
    submittedAt,
    createdAt: submittedAt,
    optimistic: true,
  };
}

async function resolveActiveTurn(threadId: string, current?: ThreadRecord | null): Promise<ThreadRecord> {
  if (current?.id === threadId && threadIsRunning(current) && current.currentTurnId) return current;

  // turn/start flips the local UI to "running" before the app-server worker has
  // necessarily published TURN_STARTED. A very fast user can already type a
  // steering message in that gap, so briefly resolve the authoritative durable
  // turn instead of accidentally targeting the previous turn id.
  for (let attempt = 0; attempt < 12; attempt += 1) {
    const snapshot = await window.loom.call<{ thread: ThreadRecord }>("thread/read", { threadId, threadOnly: true });
    if (threadIsRunning(snapshot.thread) && snapshot.thread.currentTurnId) return snapshot.thread;
    if (attempt < 11) await delay(50);
  }
  throw new Error("Loom is still starting the current turn. Try the guidance again in a moment.");
}

export function useLoom() {
  const loom = useLoomCore();
  const [optimisticStarts, setOptimisticStarts] = useState<TranscriptItem[]>([]);
  const [optimisticSteers, setOptimisticSteers] = useState<TranscriptItem[]>([]);
  const startBaselinesRef = useRef<Map<string, Set<string>>>(new Map());
  const activeThreadId = loom.active?.thread.id ?? "";
  const activeTurnHasTerminalError = useMemo(
    () => currentTurnHasTerminalError(loom.items, loom.active?.thread.currentTurnId),
    [loom.active?.thread.currentTurnId, loom.items],
  );

  // A normal turn has no client input id in the durable runtime event yet. Keep
  // the user-message ids that existed at click time and replace the local bubble
  // as soon as the first new authoritative non-steering user item arrives.
  useEffect(() => {
    setOptimisticStarts((current) => {
      let changed = false;
      const next = current.filter((item) => {
        const inputId = String(item.inputId ?? "");
        if (item.threadId !== activeThreadId) {
          startBaselinesRef.current.delete(inputId);
          changed = true;
          return false;
        }
        const baseline = startBaselinesRef.current.get(inputId) ?? new Set<string>();
        const acknowledged = loom.items.some((candidate) => (
          candidate.threadId === item.threadId
          && candidate.type === "user_message"
          && candidate.source !== "steering"
          && !baseline.has(candidate.id)
        ));
        if (acknowledged) {
          startBaselinesRef.current.delete(inputId);
          changed = true;
          return false;
        }
        return true;
      });
      return changed ? next : current;
    });
  }, [activeThreadId, loom.items]);

  // Reconcile a local steering bubble as soon as its durable USER_MESSAGE arrives.
  // The runtime uses a different durable item id, so clientInputId/inputId is the
  // stable identity across optimistic and authoritative representations.
  useEffect(() => {
    const durableInputIds = new Set(
      loom.items
        .map((item) => String(item.inputId ?? "").trim())
        .filter(Boolean),
    );
    setOptimisticSteers((current) => {
      const next = current.filter((item) => (
        item.threadId === activeThreadId
        && !durableInputIds.has(String(item.inputId ?? ""))
      ));
      return next.length === current.length && next.every((item, index) => item === current[index])
        ? current
        : next;
    });
  }, [activeThreadId, loom.items]);

  const visibleItems = useMemo(() => {
    const durableInputIds = new Set(
      loom.items
        .map((item) => String(item.inputId ?? "").trim())
        .filter(Boolean),
    );
    const pendingStarts = optimisticStarts.filter((item) => item.threadId === activeThreadId);
    const pendingSteers = optimisticSteers.filter((item) => (
      item.threadId === activeThreadId
      && !durableInputIds.has(String(item.inputId ?? ""))
    ));
    return pendingStarts.length || pendingSteers.length
      ? [...loom.items, ...pendingStarts, ...pendingSteers]
      : loom.items;
  }, [activeThreadId, loom.items, optimisticStarts, optimisticSteers]);

  const send = useCallback(async (
    input: string,
    attachments: { path: string; name: string }[] = [],
  ) => {
    const thread = loom.active?.thread;
    const text = input.trim();
    // A terminal ERROR item is an authoritative end-of-turn signal even when
    // an upstream failure skipped TURN_COMPLETED and left the thread snapshot
    // saying "running". In that state the next user message must start a fresh
    // turn, never steer the dead one.
    const running = Boolean(loom.turnActive || threadIsRunning(thread))
      && !activeTurnHasTerminalError;

    if (!running) {
      if (!thread?.id || thread.archived) return;
      if (!text && !attachments.length) return;

      const inputId = `start-${steeringInputId()}`;
      const localSubmittedAt = new Date().toISOString();
      startBaselinesRef.current.set(
        inputId,
        new Set(
          loom.items
            .filter((item) => item.threadId === thread.id && item.type === "user_message" && item.source !== "steering")
            .map((item) => item.id),
        ),
      );
      setOptimisticStarts((current) => [
        ...current.filter((item) => item.inputId !== inputId),
        optimisticStartItem(thread.id, inputId, text, attachments, localSubmittedAt),
      ]);

      try {
        await loom.send(input, attachments);
      } catch (cause) {
        startBaselinesRef.current.delete(inputId);
        setOptimisticStarts((current) => current.filter((item) => item.inputId !== inputId));
        throw cause;
      }
      return;
    }
    if (!thread?.id || thread.archived) return;
    if (!text && !attachments.length) return;

    const inputId = steeringInputId();
    const localSubmittedAt = new Date().toISOString();
    let stagedTurnId = "";

    const stage = (turnId: string) => {
      if (!turnId || stagedTurnId) return;
      stagedTurnId = turnId;
      setOptimisticSteers((current) => [
        ...current.filter((item) => item.inputId !== inputId),
        optimisticSteeringItem(thread.id, turnId, inputId, text, localSubmittedAt),
      ]);
    };

    // The normal active-turn path already knows the target id, so the bubble is
    // staged synchronously in the same click/Enter event before any RPC await.
    // The startup race still resolves the authoritative turn first.
    if (threadIsRunning(thread) && thread.currentTurnId) stage(String(thread.currentTurnId));

    try {
      const activeTurn = await resolveActiveTurn(thread.id, thread);
      const turnId = String(activeTurn.currentTurnId ?? "");
      stage(turnId);
      const receipt = await window.loom.call<SteeringReceipt>("turn/steer", {
        threadId: activeTurn.id,
        turnId,
        input: text,
        attachments,
        clientInputId: inputId,
      });

      const submittedAt = String(receipt?.submittedAt ?? "").trim();
      const displayText = String(receipt?.displayText ?? "").trim();
      if (submittedAt || displayText) {
        setOptimisticSteers((current) => current.map((item) => (
          item.inputId === inputId
            ? {
                ...item,
                ...(submittedAt ? { submittedAt, createdAt: submittedAt } : {}),
                ...(displayText ? { text: displayText } : {}),
              }
            : item
        )));
      }
    } catch (cause) {
      // Fail closed visually as well: a rejected steer must not remain in the
      // transcript as if the runtime had accepted it.
      setOptimisticSteers((current) => current.filter((item) => item.inputId !== inputId));
      throw cause;
    }
  }, [activeTurnHasTerminalError, loom.active?.thread, loom.items, loom.send, loom.turnActive]);

  return useMemo(
    () => ({ ...loom, items: visibleItems, send }),
    [loom, send, visibleItems],
  );
}
