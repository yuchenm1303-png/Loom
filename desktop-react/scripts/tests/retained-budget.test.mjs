import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";
const source = readFileSync(new URL("../../src/state/retainedBudgetMap.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { RetainedBudgetMap, retainedBytes } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("large history can be displayed without being retained in the navigation cache", () => {
  const cache = new RetainedBudgetMap(8, 4000, 2000);
  cache.set("small", { text: "hi" });
  cache.set("large", { text: "x".repeat(2000) });
  assert.equal(cache.has("large"), false);
  assert.equal(cache.has("small"), true);
});
test("byte budget evicts old entries even below the conversation count limit", () => {
  const cache = new RetainedBudgetMap(8, 1000, 900);
  cache.set("a", { text: "a".repeat(250) });
  cache.set("b", { text: "b".repeat(250) });
  assert.deepEqual([...cache.keys()], ["b"]);
  cache.delete("b");
  cache.set("a", { text: "a".repeat(250) });
  assert.equal(cache.has("a"), true);
  cache.clear();
  cache.set("c", { text: "c".repeat(250) });
  assert.equal(cache.has("c"), true);
});
test("LRU promotion reuses measured weight and cyclic snapshots terminate", () => {
  let reads = 0;
  const message = { get text() { reads++; return "unchanged"; } };
  const cache = new RetainedBudgetMap(2, 10000);
  cache.set("a", message);
  const originalReads = reads;
  cache.set("b", {});
  cache.set("a", message);
  assert.equal(reads, originalReads);
  cache.set("c", {});
  assert.deepEqual([...cache.keys()], ["a", "c"]);
  const circular = {}; circular.self = circular;
  assert.ok(Number.isFinite(retainedBytes(circular)));
});
