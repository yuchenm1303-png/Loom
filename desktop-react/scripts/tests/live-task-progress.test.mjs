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

test("committed plan event survives missing tool receipt and overrides stale receipts", () => {
  const plan = [{ id: "review", step: "Review", status: "blocked", outcome: "failed", cycles: 3 }];
  const items = [item("state", "task_plan", { turnId: "old", result: { plan, lifecycle: "active" } }),
    item("stale", "tool_call", { toolName: "update_plan", turnId: "new", result: { plan: [] } })];
  assert.deepEqual(model.latestTaskPlan(items), plan);
  assert.deepEqual(model.latestTaskPlan([...items, item("archive", "task_plan", { result: { plan, lifecycle: "archived" } })]), []);
});

test("graph action receipts work for older hosts and evidence versions are visible", () => {
  const step = { step: "Build", status: "completed", outcome: "passed", evidence_refs: [{path:"reports/result.txt"}],
    evidence_facts: [{path:"reports/result.txt",sha256:"a".repeat(64)}] };
  assert.deepEqual(model.latestTaskPlan([item("graph", "tool_call", {toolName:"manage_plan",ok:true,result:{plan:[step]}})]), [step]);
  assert.ok(model.milestoneEvidenceLabels(step)[0].includes("SHA-256 " + "a".repeat(64)));
});

test("steps a plan left out unfinished stay in view as dropped, with the reason", () => {
  // Real run: six planned steps became one "explored (negative result): completed"; training and evaluation vanished.
  const plan = { plan: [{ step: "温度头探索", status: "completed", outcome: "passed" }, { step: "更新报告", status: "pending" }],
    dropped: [{ step: "训练 nr_temphead.pt", status: "pending", reason: "冒烟结果为负" }, { bogus: true }] };
  const steps = model.latestTaskPlan([item("p", "task_plan", { result: plan })]);
  assert.deepEqual(steps.map(s => [s.step, s.status]), [["温度头探索", "completed"], ["更新报告", "pending"], ["训练 nr_temphead.pt", "dropped"]]);
  assert.equal(steps[2].blocker, "冒烟结果为负");
});

test("a reason that came with a whole replacement is not shown as the dropped step's own", () => {
  // Real run: "定位最有价值的开放问题" sat beside "已确认仓库状态…", the explanation of a list swap about other work.
  const plan = { plan: [{ step: "A", status: "pending" }, { step: "B", status: "pending" }],
    dropped: [{ step: "C", status: "pending", reason: "换清单的总说明", shared: true }, { step: "D", status: "pending", reason: "数据拿不到" }] };
  const steps = model.latestTaskPlan([item("p", "task_plan", { result: plan })]);
  assert.equal(steps[2].blocker, undefined);
  assert.equal(steps[3].blocker, "数据拿不到");
});

test("a list from an earlier request is marked as such until this turn writes its own", () => {
  // Real run: asked to sum up, the model showed the earlier request's list as this turn's and went on doing its work.
  const plan = (turnId, fields = {}) => item(`p-${turnId}`, "task_plan", { turnId,
    result: { plan: [{ step: "修正 README", status: "in_progress" }, { step: "冒烟终检", status: "pending" }], request: "修文档", ...fields } });
  const said = (turnId, source = "user") => item(`u-${turnId}`, "user_message", { turnId, source });
  const asked = model.latestTaskPlanInfo([said("t1"), plan("t1", { request_turn_id: "t1" }), said("t2")]);
  assert.equal(asked.earlier, true);
  assert.equal(asked.request, "修文档");
  assert.equal(model.latestTaskPlanInfo([said("t1"), plan("t1", { request_turn_id: "t1" })]).earlier, false);
  assert.equal(model.latestTaskPlanInfo([said("t1"), plan("t1", { request_turn_id: "t1" }), said("t2"), plan("t2", { request_turn_id: "t2" })]).earlier, false,
    "carried on with the person's say-so, or written for the new message");
  assert.equal(model.latestTaskPlanInfo([said("t1"), said("g1", "goal"), plan("g1", { request_turn_id: "t1" })]).earlier, false,
    "a goal's own continuation is not a new request");
  assert.equal(model.latestTaskPlanInfo([said("t1"), plan("t1"), said("t2")]).earlier, true, "a list from an older Loom: its own turn");
  assert.deepEqual(model.latestTaskPlan([said("t1"), plan("t1", { request_turn_id: "t1" }), said("t2")]).map(s => s.step), ["修正 README", "冒烟终检"]);
});
