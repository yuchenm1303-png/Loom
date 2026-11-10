// Which characters of a changing counter swap (src/components/tickDiff.ts).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/components/tickDiff.ts", import.meta.url), "utf8");
const module = { exports: {} };
new Function("module", "exports", ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText)(module, module.exports);
const { diffCharacters } = module.exports;

test("a counter ticking over swaps only the digit that changed", () => {
  assert.deepEqual(diffCharacters("3m 10s", "3m 11s"), { ghosts: [{ index: 4, char: "0" }], fresh: [4] });
  assert.deepEqual(diffCharacters("3m 10s", "3m 11s").whole, undefined);
});

test("a counter that gains a digit keeps its unit still and fades the new digit in", () => {
  const { ghosts, fresh } = diffCharacters("9s", "10s");
  assert.deepEqual(ghosts, [{ index: 1, char: "9" }], "the 9 becomes 0");
  assert.deepEqual([...fresh].sort(), [0, 1], "the new 1 and the swapped digit; the s does not move");
});

test("a minute turning over is one change: the old text blurs out whole over the new", () => {
  const change = diffCharacters("3m 59s", "4m 0s");
  assert.deepEqual([change.ghosts, change.fresh, change.whole], [[], [0, 1, 2, 3, 4], "3m 59s"]);
});

test("the same text swaps nothing", () => {
  assert.deepEqual(diffCharacters("12s", "12s"), { ghosts: [], fresh: [] });
});
