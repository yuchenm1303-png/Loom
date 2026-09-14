import type { ApprovalDecision, PendingApproval } from "../types/loom";
import { buildApprovalResponse } from "./approvalProtocol";

const pending = {
  requestId: "evt-approval",
  threadId: "thread-1",
  turnId: "turn-1",
  itemId: "tool:call-1",
  approvalItemId: "approval:call-1",
  callId: "call-1",
  requestType: "toolExecution",
  approvalStage: "initial",
  retryReason: null,
  startedAtMs: 1,
  toolName: "shell",
  arguments: { command: "echo ok" },
  effect: "sensitive",
  reason: "approval required",
  permissionMode: "approval",
  networkApprovalContext: null,
  availableDecisions: ["accept", "decline"],
} satisfies PendingApproval;

const accepted = buildApprovalResponse(pending, "call-1", true);
const declined = buildApprovalResponse(pending, "call-1", false);
const acceptDecision: ApprovalDecision = accepted.decision;
const declineDecision: ApprovalDecision = declined.decision;

void acceptDecision;
void declineDecision;
