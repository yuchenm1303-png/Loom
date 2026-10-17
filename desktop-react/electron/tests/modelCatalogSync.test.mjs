import assert from "node:assert/strict";
import { test } from "node:test";
import { reconcileModelCatalog } from "../../dist-electron/modelCatalogSync.js";
import { applyModelPolicy } from "../../dist-electron/modelAccess.js";

const row = (group, model) => ({ model_id: `builtin:${group}:${model}`, model, name: model,
  group_id: group, group_name: group, available: true, source: "provider" });
const payload = rows => ({ schema_version: 2, enabled: true, models: rows.map(item => item.model_id),
  decisions: rows.map(item => ({ model_id: item.model_id, enabled: true, source: "default" })),
  catalog: { revision: "new", models: structuredClone(rows), providers: [...new Set(rows.map(item => item.group_id))]
    .map(group_id => ({ group_id, last_success: 100, last_attempt: 100, error: "" })) } });
const oldGo = row("opencode-go", "omen-alpha"), beam = row("opencode-go", "beam-free");
const consoleRow = row("opencode-console", "big-pickle");

for (const corrupt of [
  data => { data.catalog.models[1].available = "yes"; },
  data => { data.catalog.models.push({ ...data.catalog.models[1] }); },
  data => { data.decisions[1].enabled = "yes"; },
  data => { data.decisions.push({ ...data.decisions[1] }); },
  data => { data.decisions.splice(1, 1); },
]) test(`invalid provider data retains only its own previous state (${corrupt.toString()})`, () => {
  const previous = payload([oldGo, consoleRow]);
  const next = payload([beam, consoleRow]);
  corrupt(next);
  const result = reconcileModelCatalog(next, previous);
  assert.deepEqual(new Set(result.catalog.models.map(item => item.model)), new Set(["big-pickle", "beam-free"]));
  assert.equal(result.catalog.providers.find(item => item.group_id === "opencode-console").client_error, "invalid-provider-catalog");
  assert.equal(result.catalog.providers.find(item => item.group_id === "opencode-go").client_error, undefined);
  assert.equal(previous.catalog.models[0].model, "omen-alpha", "reconciliation is pure");
});

test("a future provider cannot suppress newly discovered Go models or invent a route", () => {
  const result = reconcileModelCatalog(payload([beam, row("future-provider", "new")]), payload([oldGo]));
  assert.deepEqual(result.catalog.models.map(item => item.model), ["beam-free"]);
  assert.equal(result.catalog.providers.find(item => item.group_id === "future-provider").client_error, "unsupported-provider");
});

test("missing group retains its previous rows; recovery replaces them and clears the error", () => {
  const previous = payload([oldGo, consoleRow]);
  const missing = reconcileModelCatalog(payload([beam]), previous);
  assert.ok(missing.catalog.models.some(item => item.model === "big-pickle"));
  assert.equal(missing.catalog.providers.find(item => item.group_id === "opencode-console").client_error, "invalid-provider-catalog");
  const recovered = reconcileModelCatalog(payload([beam, consoleRow]), missing);
  assert.ok(recovered.catalog.providers.every(item => !item.client_error));
});

test("an invalid new group has no cached grants while valid groups still appear", () => {
  const data = payload([beam, consoleRow]);
  data.catalog.models[1].available = null;
  const result = reconcileModelCatalog(data, null);
  assert.deepEqual(result.models, [beam.model_id]);
  assert.equal(result.catalog.providers.find(item => item.group_id === "opencode-console").client_error, "invalid-provider-catalog");
  const profile = { selection: consoleRow.model_id, kind: "builtin", available: true };
  const view = applyModelPolicy({ profiles: [profile], primary: profile, current: profile }, result);
  assert.equal(view.current.accessState, "unknown", "first-start fallback must not fabricate an admin denial");
  assert.equal(view.current.available, true);
});

test("an account-wide denial remains authoritative even when a provider falls back", () => {
  const data = payload([beam, consoleRow]);
  data.enabled = false;
  data.catalog.models[1].available = null;
  const result = reconcileModelCatalog(data, payload([oldGo, consoleRow]));
  const profile = { selection: consoleRow.model_id, kind: "builtin", available: true };
  const projected = applyModelPolicy({ profiles: [profile], current: profile, primary: profile }, result);
  assert.equal(projected.current.available, false);
});

test("cross-provider identity substitution is rejected for both affected providers", () => {
  const data = payload([beam, consoleRow]);
  data.catalog.models[1].model_id = beam.model_id;
  const result = reconcileModelCatalog(data, payload([oldGo, consoleRow]));
  assert.ok(result.catalog.providers.every(item => item.client_error === "invalid-provider-catalog"));
  assert.deepEqual(new Set(result.catalog.models.map(item => item.model)), new Set(["omen-alpha", "big-pickle"]));
});
