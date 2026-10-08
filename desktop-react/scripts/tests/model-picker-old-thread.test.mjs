import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../src/App.tsx", import.meta.url), "utf8");
const core = readFileSync(new URL("../../src/state/useLoomCore.ts", import.meta.url), "utf8");
const modelPanel = readFileSync(new URL("../../src/components/ModelPanel.tsx", import.meta.url), "utf8");
const composer = readFileSync(new URL("../../src/components/ComposerBase.tsx", import.meta.url), "utf8");

test("a terminal failed old turn cannot permanently hide its model picker", () => {
  assert.match(app, /!currentTurnHasTerminalError\(loom\.items, thread\?\.currentTurnId\)/);
});

test("picker requests an authoritative short status refresh without replacing transcript", () => {
  assert.match(composer, /onRefreshActiveThread\?\.\(\)/);
  assert.match(core, /turnLimit: 1/);
  assert.match(core, /threadStateRevisionRef\.current !== revision/);
});

test("busy models show an actionable status instead of silent disabled clicks", () => {
  assert.match(modelPanel, /Another model change is in progress/);
  assert.match(modelPanel, /role="alert"/);
  assert.match(modelPanel, /disabled=\{!accountAuthenticated \|\| profile\.available === false \|\| deleting\}/);
});
