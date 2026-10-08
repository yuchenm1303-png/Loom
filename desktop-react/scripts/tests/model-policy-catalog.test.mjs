import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const main = readFileSync(new URL("../../electron/main.ts", import.meta.url), "utf8");
const manager = readFileSync(new URL("../../electron/modelManager.ts", import.meta.url), "utf8");
function functions(source, names) {
  const file = ts.createSourceFile("fixture.ts", source, ts.ScriptTarget.ES2022, true);
  const text = file.statements.filter(node => ts.isFunctionDeclaration(node) && names.includes(node.name?.text))
    .map(node => node.getText(file)).join("\n");
  const compiled = ts.transpileModule(text, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  return new Function(`${compiled}; return {${names.join(",")}};`)();
}
const { policyAllowsSelection, policyBlockedMessage, applyModelPolicy } = functions(main,
  ["modelPolicyGroup", "policyAllowsSelection", "policyBlockedMessage", "applyModelPolicy"]);
const builtin = (selection = "builtin:minimax") => ({ selection, id: selection, kind: "builtin", model: "MiniMax-M3", available: true });

test("picker denies unknown models, distinguishes retirement and admin denial, and preserves BYOK", () => {
  const access = { schema_version: 2, enabled: true, models: ["builtin:minimax"], decisions: [
    { model_id: "builtin:minimax", enabled: true, source: "global" },
    { model_id: "builtin:deepseek", enabled: false, source: "global_group" },
    { model_id: "builtin:minimax:old", enabled: false, source: "catalog" },
  ] };
  assert.equal(policyAllowsSelection(access, "builtin:minimax"), true);
  assert.equal(policyAllowsSelection(access, "builtin:minimax:invented"), false);
  assert.match(policyBlockedMessage(access, "builtin:minimax:old"), /no longer available/);
  assert.match(policyBlockedMessage(access, "builtin:deepseek"), /Loom Admin/);
  const saved = { ...builtin("saved:personal"), kind: "saved" };
  const primary = builtin("builtin:deepseek");
  const projected = applyModelPolicy({ profiles: [primary, saved], primary, current: primary }, access);
  assert.equal(projected.profiles[0].available, false);
  assert.equal(projected.current.available, false);
  assert.equal(projected.profiles[1].available, true);
  const outage = applyModelPolicy({ profiles: [primary, saved], primary, current: primary }, null);
  assert.match(outage.primary.statusMessage, /permissions unavailable/);
  assert.equal(outage.profiles[1].available, true);
});

test("legacy allow-list snapshots do not silently allow missing models", () => {
  const access = { enabled: true, models: ["builtin:minimax"] };
  assert.equal(policyAllowsSelection(access, "builtin:minimax"), true);
  assert.equal(policyAllowsSelection(access, "builtin:deepseek"), false);
});

test("server catalog adds new models in batches, fixes current status and preserves personal connections", async () => {
  const file = ts.createSourceFile("manager.ts", manager, ts.ScriptTarget.ES2022, true);
  const declaration = file.statements.find(node => ts.isClassDeclaration(node) && node.name?.text === "DesktopModelManager");
  const method = declaration.members.find(node => node.name?.getText(file) === "applyServerCatalog");
  const compiled = ts.transpileModule(`class Fixture { ${method.getText(file)} }`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const Fixture = new Function("isAntLingSelection", `${compiled}; return Fixture;`)(selection => selection.startsWith("builtin:ant-ling"));
  const fixture = new Fixture();
  fixture.serverProfiles = new Map();
  const calls = [];
  fixture.runPythonBridgeAsync = async (script, command, payload) => {
    calls.push({ script, command, count: payload.models.length });
    return payload.models.map(item => ({ ...builtin(item.selection), model: item.model, vision: false }));
  };
  const primary = builtin();
  const saved = { ...builtin("saved:personal"), kind: "saved" };
  const rows = [
    { model_id: "builtin:minimax", model: "MiniMax-M3", name: "MiniMax", group_id: "minimax", group_name: "MiniMax", available: true },
    { model_id: "builtin:ant-ling:Ling-new", model: "Ling-new", name: "New Ling", group_id: "ant-ling", group_name: "Ant Ling", available: true },
    { model_id: "builtin:deepseek:new", model: "new", name: "New DeepSeek", group_id: "deepseek", group_name: "DeepSeek", available: true },
  ];
  const snapshot = { profiles: [primary, saved], primary,
    current: { ...builtin(rows[1].model_id), available: false, statusMessage: "Local listing missing" } };
  const result = await fixture.applyServerCatalog(snapshot, rows);
  assert.deepEqual(result.profiles.map(item => item.selection), [...rows.map(item => item.model_id), saved.selection]);
  assert.equal(result.current.available, true);
  assert.equal(result.current.statusMessage, undefined);
  assert.equal(calls.length, 2);
  assert.ok(calls.every(call => call.command === "describe-models"));
  rows[1].available = false;
  const retired = await fixture.applyServerCatalog(snapshot, rows);
  assert.equal(retired.current.available, false);
  assert.equal(calls.length, 2, "known metadata is cached across permission refreshes");
});
