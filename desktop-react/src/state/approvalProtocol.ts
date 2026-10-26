import type { ApprovalDecision, PendingApproval } from "../types/loom";

export interface ApprovalResponseParams {
  [key: string]: unknown;
  threadId: string;
  turnId: string;
  requestId: string;
  callId: string;
  decision: ApprovalDecision;
  /** Why the reviewer declined. Only a decline carries one. */
  feedback?: string;
}

export const MAX_APPROVAL_FEEDBACK_CHARS = 2000;

/**
 * `choice` is one of the request's decisions; a bare boolean is the old
 * allow/deny pair. The decision must be one this very request offered.
 */
export function buildApprovalResponse(
  pending: PendingApproval | null | undefined,
  callId: string,
  choice: ApprovalDecision | boolean,
  feedback?: string,
): ApprovalResponseParams {
  if (!pending) throw new Error("This approval is no longer pending. Reload the thread.");
  if (!callId || pending.callId !== callId) {
    throw new Error("The approval item no longer matches the pending tool call. Reload the thread.");
  }
  if (!pending.turnId) {
    throw new Error("The pending approval has no turn identity and cannot be answered safely.");
  }
  if (!pending.requestId) {
    throw new Error("The pending approval has no durable request identity and cannot be answered safely.");
  }
  const decision: ApprovalDecision = typeof choice === "boolean" ? (choice ? "accept" : "decline") : choice;
  if (!pending.availableDecisions.includes(decision)) {
    throw new Error(`The pending approval does not allow the ${decision} decision.`);
  }
  const reason = (feedback ?? "").trim();
  if (reason && decision !== "decline") {
    throw new Error("Only a declined approval can carry feedback.");
  }
  if (reason.length > MAX_APPROVAL_FEEDBACK_CHARS) {
    throw new Error(`Approval feedback must be at most ${MAX_APPROVAL_FEEDBACK_CHARS} characters.`);
  }
  return {
    threadId: pending.threadId,
    turnId: pending.turnId,
    requestId: pending.requestId,
    callId: pending.callId,
    decision,
    ...(reason ? { feedback: reason } : {}),
  };
}
