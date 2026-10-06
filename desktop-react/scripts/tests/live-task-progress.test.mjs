import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/components/liveTaskProgress.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const model = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const item = (id, type, fields = {}) => ({ id, type, status: "completed", ...fields });

test("live view folds old commentary and completed tools without losing any items", () => {
  const items = [item("old", "assistant_message", { phase: "commentary" }), item("tool", "tool_call"),
    item("new", "assistant_message", { phase: "commentary" }), item("running", "tool_call", { status: "running" })];
  const { earlier, current } = model.liveTaskProgress(items);
  assert.deepEqual(earlier.map(i => i.id), ["old", "tool"]);
  assert.deepEqual(current.map(i => i.id), ["new", "running"]);
  assert.equal(new Set([...earlier, ...current].map(i => i.id)).size, items.length);
});

test("steering, approvals, failures, decisions and explicit finals stay visible", () => {
  const protectedItems = [item("steer", "user_message"), item("approval", "approval"),
    item("error", "error"), item("fail", "tool_call", { status: "failed" }),
    item("decision", "assistant_message", { phase: "commentary" }),
    item("final", "assistant_message", { phase: "final_answer" })];
  const result = model.liveTaskProgress([...protectedItems, item("new", "assistant_message", { phase: "commentary" })], new Set(["decision"]));
  assert.equal(result.earlier.length, 0);
  assert.equal(result.current.length, 7);
});

test("latest successful durable plan wins; failed update cannot overwrite it", () => {
  const plan = [{ step: "Checks", status: "completed", outcome: "interrupted" }, { step: "Report", status: "blocked", blocker: "server offline" }];
  assert.deepEqual(model.latestTaskPlan([
    item("plan", "tool_call", { toolName: "update_plan", result: { plan } }),
    item("failed", "tool_call", { toolName: "update_plan", status: "failed", result: { plan: [] } }),
  ]), plan);
});

test("milestone evidence is displayed separately from acceptance and never inferred as passed", () => {
  const step = { step: "Pressure", status: "completed", outcome: "interrupted", evidence_refs: [{call_id:"call-17"}, {path:"reports/result.json"}], evidence:"Connection closed" };
  assert.deepEqual(model.milestoneEvidenceLabels(step), ["工具记录：call-17", "文件：reports/result.json"]);
  assert.equal(model.milestoneOutcomeLabel(step), "测试中断");
  assert.equal(model.milestoneOutcomeLabel({step:"Legacy",status:"completed"}), "未记录验收结论");
  assert.deepEqual(model.latestTaskPlan([item("plan", "tool_call", {toolName:"update_plan",result:{plan:[step]}})])[0].evidence_refs, step.evidence_refs);
});
