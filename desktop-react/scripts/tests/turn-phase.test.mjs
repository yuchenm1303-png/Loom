import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/components/turnPhase.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { turnPhaseLabel } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("waiting stages describe actual work in both languages", () => {
  assert.match(turnPhaseLabel("compacting", "zh"), /历史上下文/);
  assert.match(turnPhaseLabel("preparing", "en"), /Preparing/);
  assert.match(turnPhaseLabel("waiting_model", "en"), /Waiting/);
  assert.match(turnPhaseLabel("retrying", "zh"), /重新连接/);
  assert.equal(turnPhaseLabel("running", "zh"), undefined);
  assert.equal(turnPhaseLabel(undefined, "en"), undefined);
  assert.equal(turnPhaseLabel("unknown-future-phase", "en"), undefined);
});
