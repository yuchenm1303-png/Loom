import { test } from "node:test";
import assert from "node:assert/strict";
import { buildSync } from "esbuild";
import { fileURLToPath } from "node:url";

const source = buildSync({
  entryPoints: [fileURLToPath(new URL("../../src/state/approvalProtocol.ts", import.meta.url))],
  bundle: true, platform: "node", format: "esm", write: false,
}).outputFiles[0].text;
const { buildApprovalResponse } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

const pending = {
  requestId: "evt-approval", threadId: "thread-1", turnId: "turn-1", itemId: "tool:call-1",
  approvalItemId: "approval:call-1", callId: "call-1", requestType: "toolExecution", approvalStage: "initial",
  retryReason: null, startedAtMs: 1, toolName: "exec", arguments: {}, effect: "sensitive", reason: "approval required",
  permissionMode: "approval", networkApprovalContext: null, availableDecisions: ["accept", "decline"],
};
const scoped = {
  ...pending,
  availableDecisions: ["accept", "acceptForSession", "acceptWithPrefixRule", "decline"],
  proposedPrefixRule: ["git", "status"], sessionScope: { kind: "prefix", prefix: ["git", "status"] }, ruleSandbox: "policy",
};

test("the old allow/deny pair keeps working and keeps the durable identity", () => {
  assert.deepEqual(buildApprovalResponse(pending, "call-1", true), {
    threadId: "thread-1", turnId: "turn-1", requestId: "evt-approval", callId: "call-1", decision: "accept",
  });
  assert.equal(buildApprovalResponse(pending, "call-1", false).decision, "decline");
});

test("a wider grant is sent only when this very request offered it", () => {
  assert.equal(buildApprovalResponse(scoped, "call-1", "acceptForSession").decision, "acceptForSession");
  assert.equal(buildApprovalResponse(scoped, "call-1", "acceptWithPrefixRule").decision, "acceptWithPrefixRule");
  assert.throws(() => buildApprovalResponse(pending, "call-1", "acceptForSession"), /does not allow the acceptForSession/);
  assert.throws(() => buildApprovalResponse(pending, "call-1", "acceptWithPrefixRule"), /does not allow/);
});

test("only a decline carries an explanation, trimmed and bounded", () => {
  const explained = buildApprovalResponse(pending, "call-1", "decline", "  Use a pull request instead.  ");
  assert.equal(explained.decision, "decline");
  assert.equal(explained.feedback, "Use a pull request instead.");
  // Blank means no feedback field at all, not an empty one.
  assert.equal("feedback" in buildApprovalResponse(pending, "call-1", "decline", "   "), false);
  assert.equal("feedback" in buildApprovalResponse(pending, "call-1", "decline"), false);
  assert.throws(() => buildApprovalResponse(scoped, "call-1", "accept", "fine"), /Only a declined approval/);
  assert.throws(() => buildApprovalResponse(pending, "call-1", "decline", "x".repeat(2001)), /at most 2000/);
  assert.equal(buildApprovalResponse(pending, "call-1", "decline", "x".repeat(2000)).feedback.length, 2000);
});

test("a response never targets a request the reviewer was not shown", () => {
  assert.throws(() => buildApprovalResponse(null, "call-1", true), /no longer pending/);
  assert.throws(() => buildApprovalResponse(pending, "call-2", true), /no longer matches/);
  assert.throws(() => buildApprovalResponse({ ...pending, requestId: null }, "call-1", true), /durable request identity/);
  assert.throws(() => buildApprovalResponse({ ...pending, turnId: null }, "call-1", true), /turn identity/);
});
