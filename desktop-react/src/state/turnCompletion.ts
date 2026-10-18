import type { ThreadRecord, ThreadStatus, TranscriptItem, TurnRecord } from "../types/loom";

export interface TerminalTurn {
  threadId: string;
  turnId: string;
  status: ThreadStatus;
}

export function terminalStatus(status?: string): status is ThreadStatus {
  return ["completed", "failed", "cancelled", "interrupted", "limit_reached"].includes(status ?? "");
}

/** Only explicit Host lifecycle records end a run; never infer it from prose. */
export function reconcileThreadCompletion(thread: ThreadRecord, turns: TurnRecord[] = [], known: TerminalTurn | null = null) {
  const turnId = String(thread.currentTurnId ?? "");
  const terminal = known?.threadId === thread.id && known.turnId === turnId ? known
    : turns.filter(turn => turn.id === turnId && terminalStatus(turn.status)).map(turn => ({
        threadId: thread.id, turnId, status: turn.status as ThreadStatus,
      })).at(-1)
      ?? (turnId && terminalStatus(thread.status) ? { threadId: thread.id, turnId, status: thread.status } : null);
  return { terminal, thread: terminal && (thread.status === "running" || thread.status === "waiting_approval")
    ? { ...thread, status: terminal.status } : thread };
}

/** Apply the Host's final-item identity and release retained streaming flags. */
export function settleCompletedTurnItems(items: TranscriptItem[], turn: TurnRecord): TranscriptItem[] {
  if (!terminalStatus(turn.status)) return items;
  return items.map(item => {
    if (item.turnId !== turn.id || item.type !== "assistant_message") return item;
    const final = turn.status === "completed" && Boolean(
      (turn.finalItemId && item.id === turn.finalItemId)
      || (turn.finalStepId && item.stepId === turn.finalStepId));
    const phase = final ? "final_answer" : item.phase;
    const status = ["started", "running", "streaming"].includes(item.status ?? "")
      ? turn.status === "completed" ? "completed" : "interrupted" : item.status;
    return phase === item.phase && status === item.status ? item : { ...item, phase, status };
  });
}
