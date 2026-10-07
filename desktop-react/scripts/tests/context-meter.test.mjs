import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";
const source = readFileSync(new URL("../../src/components/contextMeterModel.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { contextMeterModel } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const legacy = { windowKnown: false, windowTokens: 272000, inputBudgetTokens: 254304,
  usedTokens: 377721, usedPercent: 100, autoCompactTokens: 244800 };
test("legacy fallback capacity cannot render a full meter", () => {
  assert.deepEqual(contextMeterModel(legacy), { budget: null, percent: null, unknown: true,
    autoCompactTokens: null, windowTokens: null });
});
test("declared capacity preserves actual overruns rather than clipping the label", () => {
  const result = contextMeterModel({ ...legacy, windowKnown: true, inputBudgetTokens: 100000, usedTokens: 110000 });
  assert.equal(result.percent, 110);
  assert.equal(result.unknown, false);
});
test("working and observed budgets stay usable without inventing a model window", () => {
  for (const budgetBasis of ["working", "observed"]) {
    const result = contextMeterModel({ ...legacy, budgetBasis, inputBudgetTokens: 400000 });
    assert.equal(result.percent, 94);
    assert.equal(result.windowTokens, null);
  }
});
test("zero or missing budget is unknown even if metadata is malformed", () => {
  for (const inputBudgetTokens of [0, null, undefined]) {
    assert.equal(contextMeterModel({ ...legacy, windowKnown: true, inputBudgetTokens }).unknown, true);
  }
});
