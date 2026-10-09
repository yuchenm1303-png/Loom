import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/components/processNote.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { isLongNote } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("a one-sentence note is never clamped", () => {
  assert.equal(isLongNote("原生 select 通过：sel1.value=b。继续：自定义下拉。"), false);
  assert.equal(isLongNote("点 Y。"), false);
});

test("length is the only density signal: 420 characters fits, 421 clamps", () => {
  assert.equal(isLongNote("字".repeat(420)), false);
  assert.equal(isLongNote("字".repeat(421)), true);
});

test("many short lines clamp even when the character count is small", () => {
  assert.equal(isLongNote("1\n2\n3\n4\n5\n6"), false);
  assert.equal(isLongNote("1\n2\n3\n4\n5\n6\n7"), true);
});

test("surrounding whitespace never makes a note long", () => {
  assert.equal(isLongNote("\n\n  先读真实 rect。  \n\n\n\n"), false);
});

test("missing text is not long", () => {
  assert.equal(isLongNote(undefined), false);
  assert.equal(isLongNote(null), false);
  assert.equal(isLongNote(""), false);
});
