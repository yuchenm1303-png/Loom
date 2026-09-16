import { useCallback, useMemo } from "react";
import type { ThreadReadResult, ThreadRecord } from "../types/loom";
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

    const activeTurn = await resolveActiveTurn(thread.id, thread);
    await window.loom.call("turn/steer", {
      threadId: activeTurn.id,
      turnId: activeTurn.currentTurnId,
      input: text,
      clientInputId: steeringInputId(),
    });
  }, [loom.active?.thread, loom.send, loom.turnActive]);

  return useMemo(() => ({ ...loom, send }), [loom, send]);
}
