import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const compile = async name => {
  const source = readFileSync(new URL(`../../src/components/${name}.ts`, import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
};
const { planUpdateNoteIds } = await compile("executionSequence");

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
