import test from "node:test";
import assert from "node:assert/strict";
import ts from "typescript";
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../src/App.tsx", import.meta.url), "utf8");
const core = readFileSync(new URL("../../src/state/useLoomCore.ts", import.meta.url), "utf8");
const modelPanel = readFileSync(new URL("../../src/components/ModelPanel.tsx", import.meta.url), "utf8");
const composer = readFileSync(new URL("../../src/components/ComposerBase.tsx", import.meta.url), "utf8");

function projectThread() {
  const source = ts.createSourceFile("core.ts", core, ts.ScriptTarget.ES2022, true);
  const declaration = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === "modelsForThread");
  const compiled = ts.transpileModule(declaration.getText(source), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  return new Function(`${compiled}; return modelsForThread;`)();
}

test("old thread binding stays explicit during catalog sync and never inherits a different connection", () => {
  const project = projectThread();
  const thread = { modelSelection: "builtin:opencode-go:demo", model: "demo", modelProvider: "old-provider", modelBaseUrl: "https://old-provider.test" };
  const snapshot = { profiles: [], current: { selection: "builtin:minimax" }, policySync: { state: "unavailable" } };
  const unknown = project(snapshot, thread);
  assert.equal(unknown.current.selection, thread.modelSelection);
  assert.equal(unknown.current.catalogState, "unknown");
  assert.equal(unknown.current.available, true);
  assert.equal(project({ ...snapshot, policySync: { state: "fresh" } }, thread).current.available, false);
  const saved = project(snapshot, { ...thread, modelSelection: "saved:legacy" });
  assert.equal(saved.current.kind, "saved");
  assert.equal(saved.current.available, false, "a removed personal connection cannot be substituted with a builtin");
  const profile = { selection: thread.modelSelection, adapter: "opencode-go", baseUrl: "https://account.smirel.com/model/v1/opencode-go", kind: "builtin" };
  const resolved = project({ ...snapshot, profiles: [profile] }, thread);
  assert.equal(resolved.current.baseUrl, profile.baseUrl);
  assert.equal(resolved.current.provider, profile.adapter);
});

test("a terminal failed old turn cannot permanently hide its model picker", () => {
  assert.match(app, /!currentTurnHasTerminalError\(loom\.items, thread\?\.currentTurnId\)/);
});

test("picker requests an authoritative short status refresh without replacing transcript", () => {
  assert.match(composer, /onRefreshActiveThread\?\.\(\)/);
  assert.match(core, /turnLimit: 1/);
  assert.match(core, /threadStateRevisionRef\.current !== revision/);
});

test("pending switches keep models selectable and failures visible after closing", () => {
  assert.match(composer, /modelError.*role="alert"/);
  assert.match(modelPanel, /role="alert"/);
  assert.match(modelPanel, /disabled=\{!accountAuthenticated \|\| profile\.available === false \|\| deleting\}/);
});


test("selecting a model closes the picker before the Host acknowledges it", async () => {
  const source = ts.createSourceFile("picker.tsx", modelPanel, ts.ScriptTarget.ES2022, true, ts.ScriptKind.TSX);
  let declaration;
  function visit(node) {
    if (ts.isFunctionDeclaration(node) && node.name?.text === "chooseProfile") declaration = node;
    ts.forEachChild(node, visit);
  }
  visit(source);
  assert.ok(declaration);
  const compiled = ts.transpileModule(declaration.getText(source), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  let reject;
  const acknowledgement = new Promise((_, fail) => { reject = fail; });
  const events = [];
  const choose = new Function("accountAuthenticated", "setError", "isCurrentProfile", "onSwitchProfile", "onClose", `${compiled}; return chooseProfile;`)(
    true, message => { throw new Error(message); }, () => false,
    selection => { events.push(selection); return acknowledgement; }, () => events.push("closed"),
  );
  await choose({ selection: "builtin:minimax", available: true });
  assert.deepEqual(events, ["builtin:minimax", "closed"]);
  reject(new Error("Host unavailable"));
  await new Promise(resolve => setImmediate(resolve));
});
