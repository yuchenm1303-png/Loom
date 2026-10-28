import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";
const source = readFileSync(new URL("../../src/components/projectRuns.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { actions, effectiveRun, budgetValues, ProjectCommand } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const run = { id: "root", state: "running", control: "active", completionPolicy: "local_integrate", workflowControl: "active", plan: [] };
test("lost responses retry the exact operation and frozen revision", async () => {
  const calls = [];
  const command = new ProjectCommand(async (method, params) => {
    calls.push({ method, params });
    if (calls.length === 1) throw new Error("response lost after execution");
    return { run };
  }, () => "stable-id");
  await assert.rejects(command.send("projectRun/resume", { runId: "root", expectedRevision: 9, tokenBudget: 200 }), /lost/);
  await assert.rejects(command.send("projectRun/create", {}), /previous/);
  assert.equal(await command.send(), run);
  assert.deepEqual(calls[0], calls[1]);
  assert.equal(calls[1].params.expectedRevision, 9);
  assert.equal(command.pending, null);
});
test("double submission cannot create concurrent commands", async () => {
  let release;
  let calls = 0;
  const command = new ProjectCommand(() => { calls++; return new Promise(resolve => { release = resolve; }); });
  const first = command.send("projectRun/create", { objective: "goal" });
  await assert.rejects(command.send(), /already running/);
  release({ run }); await first;
  assert.equal(calls, 1);
});
test("controls follow owner, child review and unknown execution boundaries", () => {
  assert.deepEqual(actions(run), ["pause", "cancel"]);
  assert.deepEqual(actions({ ...run, workflowStatus: "blocked" }, { state: "needs_review" }), ["cancel"]);
  assert.deepEqual(actions({ ...run, workflowControl: "paused" }), ["resume", "cancel"]);
  assert.deepEqual(actions({ ...run, state: "integrated" }), []);
  assert.deepEqual(actions({ ...run, workflowOwnerId: "other", state: "needs_review", plan: [{}], activeEpisode: { state: "outcome_unknown" } }), []);
  assert.deepEqual(actions({ ...run, workflowOwnerId: "other", state: "needs_review", plan: [{}] }), ["review"]);
});
test("manual candidate never offers delivery without independent acceptance", () => {
  const candidate = { ...run, completionPolicy: "manual", state: "ready_for_review", plan: [{}] };
  assert.deepEqual(actions(candidate), ["review"]);
  assert.deepEqual(actions({ ...candidate, verificationStatus: "passed" }), ["deliver", "review"]);
});
test("live child is the usage ledger, not stale root counters", () => {
  const child = { id: "child", tokensUsed: 80 };
  assert.equal(effectiveRun({ ...run, integrationRunId: "child", tokensUsed: 10 }, [child]), child);
  assert.equal(effectiveRun(run, []), run);
});
test("budgets reject empty, fractional, nonfinite and out of range totals", () => {
  assert.deepEqual(budgetValues("200000", "100"), { tokenBudget: 200000, maxEpisodes: 100 });
  for (const values of [["", "1"], ["Infinity", "10"], ["1", "1.5"], ["1", "10001"], ["1000000001", "1"]])
    assert.throws(() => budgetValues(...values));
});
