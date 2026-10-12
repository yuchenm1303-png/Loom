import test from "node:test";
import assert from "node:assert/strict";
import ts from "typescript";
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
