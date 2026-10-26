import type { ApprovalDecision, PendingApproval } from "../types/loom";
import { buildApprovalResponse } from "./approvalProtocol";

// Compile-time contract only; scripts/tests/approval-protocol.test.mjs runs the behavior.

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

const scoped = {
  ...pending,
  availableDecisions: ["accept", "acceptForSession", "acceptWithPrefixRule", "decline"],
  proposedPrefixRule: ["git", "status"],
  sessionScope: { kind: "prefix", prefix: ["git", "status"] },
  ruleSandbox: "policy",
} satisfies PendingApproval;

// The old allow/deny pair still maps onto the first and last decision.
const accepted = buildApprovalResponse(pending, "call-1", true);
const declined = buildApprovalResponse(pending, "call-1", false);
const acceptDecision: ApprovalDecision = accepted.decision;
const declineDecision: ApprovalDecision = declined.decision;

// A wider grant, and a refusal that says why.
const remembered = buildApprovalResponse(scoped, "call-1", "acceptForSession");
const saved = buildApprovalResponse(scoped, "call-1", "acceptWithPrefixRule");
const explained = buildApprovalResponse(pending, "call-1", "decline", "Use a pull request instead.");
const rememberedDecision: ApprovalDecision = remembered.decision;
const savedDecision: ApprovalDecision = saved.decision;
const reason: string | undefined = explained.feedback;

void acceptDecision;
void declineDecision;
void rememberedDecision;
void savedDecision;
void reason;
