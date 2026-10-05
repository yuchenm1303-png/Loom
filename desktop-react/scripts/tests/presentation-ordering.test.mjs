import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/components/presentationOrdering.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { deferredActivityIndices } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const prose = (id) => ({ kind: "item", item: { id, type: "assistant_message" } });
const activity = { kind: "activity" };

test("revealed tools stay mounted when preceding prose resumes painting", () => {
  const first = { kind: "activity", items: [{ id: "tool-1" }] };
  const next = { kind: "activity", items: [{ id: "tool-2" }] };
  const blocks = [prose("a"), first, prose("b"), next];
  const revealed = new Set(["tool-1"]);
  assert.deepEqual([...deferredActivityIndices(blocks, new Set(), revealed)], []);
  assert.deepEqual([...deferredActivityIndices(blocks, new Set(["a:answer"]), revealed)], [3]);
  assert.deepEqual([...deferredActivityIndices(blocks, new Set(["a:reasoning"]), revealed)], [3]);
  // Appending rows to a revealed batch must preserve its disclosure state too.
  first.items.push({ id: "tool-3" });
  assert.deepEqual([...deferredActivityIndices(blocks, new Set(["a:answer"]), revealed)], [3]);
});

test("tool events wait for preceding prose to finish painting", () => {
  const blocks = [prose("a"), activity];
  assert.deepEqual([...deferredActivityIndices(blocks, new Set(["a:answer"]))], [1]);
  assert.deepEqual([...deferredActivityIndices(blocks, new Set())], []);
});

test("later prose never hides earlier activity; reasoning also holds later groups", () => {
  const blocks = [activity, prose("b"), activity, prose("c"), activity];
  assert.deepEqual([...deferredActivityIndices(blocks, new Set(["b:reasoning"]))], [2, 4]);
  assert.deepEqual([...deferredActivityIndices(blocks, new Set(["c:answer"]))], [4]);
});

test("approval cards remain available while prose drains; unrelated messages do not block", () => {
  const blocks = [prose("a"), { kind: "item", item: { id: "approval", type: "approval" } }, activity];
  assert.deepEqual([...deferredActivityIndices(blocks, new Set(["a:answer"]))], [2]);
  assert.deepEqual([...deferredActivityIndices(blocks, new Set(["other:answer"]))], []);
});
