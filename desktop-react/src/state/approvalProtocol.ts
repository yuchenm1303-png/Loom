import type { ApprovalDecision, PendingApproval } from "../types/loom";

export interface ApprovalResponseParams {
  threadId: string;
  turnId: string;
  requestId: string;
  callId: string;
  decision: ApprovalDecision;
}

export function buildApprovalResponse(
  pending: PendingApproval | null | undefined,
  callId: string,
  approved: boolean,
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
  const decision: ApprovalDecision = approved ? "accept" : "decline";
  if (!pending.availableDecisions.includes(decision)) {
    throw new Error(`The pending approval does not allow the ${decision} decision.`);
  }
  return {
    threadId: pending.threadId,
    turnId: pending.turnId,
    requestId: pending.requestId,
    callId: pending.callId,
    decision,
  };
}
