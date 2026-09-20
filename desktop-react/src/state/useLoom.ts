import { useCallback, useEffect, useMemo, useState } from "react";
import type { ThreadReadResult, ThreadRecord, TranscriptItem } from "../types/loom";
import { useLoom as useLoomCore } from "./useLoomCore";

function threadIsRunning(thread?: ThreadRecord | null): boolean {
  return thread?.status === "running" || thread?.status === "waiting_approval";
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
};

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
    const snapshot = await window.loom.call<ThreadReadResult>("thread/read", { threadId });
    if (threadIsRunning(snapshot.thread) && snapshot.thread.currentTurnId) return snapshot.thread;
    if (attempt < 11) await delay(50);
  }
  throw new Error("Loom is still starting the current turn. Try the guidance again in a moment.");
}

export function useLoom() {
  const loom = useLoomCore();
  const [optimisticSteers, setOptimisticSteers] = useState<TranscriptItem[]>([]);
  const activeThreadId = loom.active?.thread.id ?? "";

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
    const pending = optimisticSteers.filter((item) => (
      item.threadId === activeThreadId
      && !durableInputIds.has(String(item.inputId ?? ""))
    ));
    return pending.length ? [...loom.items, ...pending] : loom.items;
  }, [activeThreadId, loom.items, optimisticSteers]);

  const send = useCallback(async (
    input: string,
    attachments: { path: string; name: string }[] = [],
  ) => {
    const thread = loom.active?.thread;
    const text = input.trim();
    const running = Boolean(loom.turnActive || threadIsRunning(thread));

    if (!running) {
      await loom.send(input, attachments);
      return;
    }
    if (!thread?.id || thread.archived) return;
    if (!text) return;
    if (attachments.length) {
      throw new Error("Attachments cannot be added while steering an active turn. Send them in the next turn.");
    }

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
        clientInputId: inputId,
      });

      const submittedAt = String(receipt?.submittedAt ?? "").trim();
      if (submittedAt) {
        setOptimisticSteers((current) => current.map((item) => (
          item.inputId === inputId
            ? { ...item, submittedAt, createdAt: submittedAt }
            : item
        )));
      }
    } catch (cause) {
      // Fail closed visually as well: a rejected steer must not remain in the
      // transcript as if the runtime had accepted it.
      setOptimisticSteers((current) => current.filter((item) => item.inputId !== inputId));
      throw cause;
    }
  }, [loom.active?.thread, loom.send, loom.turnActive]);

  return useMemo(
    () => ({ ...loom, items: visibleItems, send }),
    [loom, send, visibleItems],
  );
}
