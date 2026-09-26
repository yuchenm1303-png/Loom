import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/components/presentationOrdering.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { deferredActivityIndices } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const prose = (id) => ({ kind: "item", item: { id, type: "assistant_message" } });
const activity = { kind: "activity" };

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
