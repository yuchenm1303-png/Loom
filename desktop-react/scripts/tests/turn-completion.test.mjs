import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/state/turnCompletion.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { reconcileThreadCompletion, settleCompletedTurnItems } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const thread = { id: "thread", currentTurnId: "turn", status: "running" };

test("completed Host turn overrides a stale running thread snapshot", () => {
  const result = reconcileThreadCompletion(thread, [{ id: "turn", status: "completed" }]);
  assert.equal(result.thread.status, "completed");
  assert.equal(reconcileThreadCompletion(thread, [], result.terminal).thread.status, "completed");
});

test("terminal history and markers never end another turn or another thread", () => {
  const known = { threadId: "thread", turnId: "old", status: "completed" };
  assert.equal(reconcileThreadCompletion(thread, [{ id: "old", status: "completed" }], known).thread.status, "running");
  assert.equal(reconcileThreadCompletion(thread, [], { ...known, threadId: "other", turnId: "turn" }).thread.status, "running");
});

test("all terminal states are sticky while genuine running/approval states remain live", () => {
  for (const status of ["completed", "failed", "cancelled", "interrupted", "limit_reached"]) {
    const known = reconcileThreadCompletion({ ...thread, status }).terminal;
    assert.equal(reconcileThreadCompletion({ ...thread, status: "waiting_approval" }, [], known).thread.status, status);
  }
  for (const status of ["running", "waiting_approval"]) {
    assert.equal(reconcileThreadCompletion({ ...thread, status }, [{ id: "turn", status }]).terminal, null);
  }
});

test("final-item identity settles retained streaming flags without guessing from prose", () => {
  const items = [{ id: "answer", type: "assistant_message", turnId: "turn", phase: "commentary", status: "streaming", text: "Done." },
    { id: "other", type: "assistant_message", turnId: "next", phase: "commentary", status: "streaming" }];
  const settled = settleCompletedTurnItems(items, { id: "turn", status: "completed", finalItemId: "answer" });
  assert.equal(settled[0].phase, "final_answer");
  assert.equal(settled[0].status, "completed");
  assert.equal(settled[1], items[1]);
  assert.equal(settleCompletedTurnItems(items, { id: "turn", status: "running" }), items);
  assert.equal(reconcileThreadCompletion(thread).thread.status, "running");
});

test("failed turns release streaming but do not promote messages to final answers", () => {
  const items = [{ id: "a", type: "assistant_message", turnId: "turn", status: "streaming", phase: "commentary" }];
  assert.deepEqual(settleCompletedTurnItems(items, { id: "turn", status: "failed" })[0], { ...items[0], status: "interrupted" });
});
