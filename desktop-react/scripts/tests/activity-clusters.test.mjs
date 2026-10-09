import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const compile = async name => {
  const source = readFileSync(new URL(`../../src/components/${name}.ts`, import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
};
const { runsOf, CLUSTER_MIN_ROWS } = await compile("activityClusters");
const { planUpdateNoteIds } = await compile("executionSequence");

const rows = kinds => kinds.map((kind, index) => ({ id: `r${index}`, kind }));
const summary = runs => runs.map(run => `${run.key}:${run.entries.length}@${run.id}`);

test("consecutive entries of one kind form a run that keeps the id of its first entry", () => {
  const runs = runsOf(rows(["search", "search", "search", "command", "search"]), row => row.kind, row => row.id);
  assert.deepEqual(summary(runs), ["search:3@r0", "command:1@r3", "search:1@r4"]);
});

test("a growing run keeps its identity so the summary line does not remount", () => {
  const before = runsOf(rows(["a", "a", "a"]), row => row.kind, row => row.id);
  const after = runsOf(rows(["a", "a", "a", "a", "a"]), row => row.kind, row => row.id);
  assert.equal(before[0].id, after[0].id);
  assert.equal(after[0].entries.length, 5);
});

test("different kinds never merge, and nothing is dropped or reordered", () => {
  const input = rows(["x", "y", "x", "y", "y", "z"]);
  const runs = runsOf(input, row => row.kind, row => row.id);
  assert.deepEqual(runs.flatMap(run => run.entries), input);
  assert.deepEqual(runs.map(run => run.key), ["x", "y", "x", "y", "z"]);
});

test("an empty list yields no runs, and a pair is below the collapse threshold", () => {
  assert.deepEqual(runsOf([], row => row.kind, row => row.id), []);
  assert.equal(CLUSTER_MIN_ROWS, 3);
});

const item = (id, type, extra = {}) => ({ id, type, turnId: "t", status: "completed", ...extra });
const message = (id, stepId, extra = {}) => item(id, "assistant_message", { phase: "commentary", text: id, stepId, ...extra });

test("a sentence sent in the same model step as a plan update is the model's stage report", () => {
  const input = [message("a", "s1"), item("t1", "tool_call", { toolName: "exec", stepId: "s1" }),
    message("b", "s2"), item("plan", "tool_call", { toolName: "update_plan", stepId: "s2" }), message("c", "s3"), item("t3", "tool_call", { toolName: "exec", stepId: "s3" })];
  assert.deepEqual([...planUpdateNoteIds(input)], ["b"]);
});

test("a plan update without its own sentence never promotes a neighbouring step's sentence", () => {
  const input = [message("earlier", "s1"), item("t1", "tool_call", { toolName: "exec", stepId: "s1" }), item("plan", "tool_call", { toolName: "update_plan", stepId: "s2" })];
  assert.deepEqual([...planUpdateNoteIds(input)], []);
});

test("steps without identity are never linked, and only commentary can be promoted", () => {
  const input = [message("a", undefined), item("plan", "tool_call", { toolName: "update_plan" }),
    message("final", "s9", { phase: "final_answer" }), item("plan2", "tool_call", { toolName: "update_plan", stepId: "s9" })];
  assert.deepEqual([...planUpdateNoteIds(input)], []);
});
