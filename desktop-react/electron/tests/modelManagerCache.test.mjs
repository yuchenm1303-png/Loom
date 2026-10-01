import assert from "node:assert/strict";
import { test } from "node:test";
import { DesktopModelManager } from "../../dist-electron/modelManager.js";

function profile(overrides = {}) {
  return {
    selection: "saved:demo", id: "demo", kind: "saved", name: "Demo",
    adapter: "openai-compatible", baseUrl: "https://example.invalid/v1", model: "demo-1",
    provider: "demo", apiKey: "sk-old", configured: true, available: true,
    catalogSource: "provider", vision: true, contextLimits: { contextWindowTokens: 100000 },
    authMode: "loom-account", ...overrides,
  };
}
function registry(rows) { return { primary: rows[0] ?? profile(), profiles: rows, activeModelId: rows[0]?.id ?? null }; }
function managerWithFake() {
  const m = new DesktopModelManager("/tmp");
  const state = {
    rows: [profile()], metadata: { profiles: [profile()] }, resolved: profile(),
    listCalls: 0, resolveCalls: 0,
  };
  m.runPythonBridge = (script, command, payload) => {
    if (script === "loom_ant_ling_bridge.py" && command === "list") return { profiles: [], activeSelection: null };
    if (script === "loom_model_admin.py" && command === "metadata") return state.metadata;
    if (script === "loom_model_bridge.py" && command === "list") { state.listCalls += 1; return registry(state.rows); }
    if (script === "loom_model_bridge.py" && command === "resolve") { state.resolveCalls += 1; return { ...state.resolved, selection: payload.selection }; }
    if (script === "loom_model_bridge.py" && command === "set-provider-key") { state.resolved = { ...state.resolved, apiKey: payload.apiKey }; return { provider: payload.provider, configured: true }; }
    if (script === "loom_model_bridge.py" && command === "set-reasoning") return { ...state.resolved, reasoning: { kind: payload.kind, value: payload.value, defaultValue: "medium", options: [], source: "test" } };
    if (script === "loom_model_bridge.py" && command === "describe-model") return { ...state.resolved, model: payload.model, reasoning: null };
    throw new Error(`unexpected bridge call ${script}:${command}`);
  };
  m.runPythonBridgeAsync = async (script, command, payload) => m.runPythonBridge(script, command, payload);
  return { m, state };
}

test("sync registry honors TTL instead of serving an unbounded cache", () => {
  const { m, state } = managerWithFake();
  m.registry();
  assert.equal(state.listCalls, 1);
  m.registry();
  assert.equal(state.listCalls, 1, "fresh registry should hit cache");
  m.registryCacheMonotonicAt = -1_000_000_000;
  m.registry();
  assert.equal(state.listCalls, 2, "expired registry must re-fetch on sync path");
});

test("catalog refresh re-projects surviving cached launch specs without re-resolving", () => {
  const { m, state } = managerWithFake();
  m.registry();
  m.useProfile("saved:demo");
  assert.equal(state.resolveCalls, 1);
  state.rows = [profile({ available: false, contextLimits: { contextWindowTokens: 400000 }, authMode: "loom-account-v2" })];
  state.metadata = { profiles: state.rows };
  m.registry(true);
  const next = m.resolve("saved:demo");
  assert.equal(state.resolveCalls, 1, "catalog-only refresh must not respawn resolve bridge");
  assert.equal(next.available, false);
  assert.equal(next.contextLimits.contextWindowTokens, 400000);
  assert.equal(next.authMode, "loom-account-v2");
  assert.equal(m.current.contextLimits.contextWindowTokens, 400000, "active spec must refresh too");
});

test("explicit model and reasoning choices survive catalog re-projection", () => {
  const { m, state } = managerWithFake();
  m.registry();
  m.useProfile("saved:demo");
  m.useModelName("demo-custom");
  m.setReasoning("openai-effort", "high");
  state.rows = [profile({ model: "provider-default-2", contextLimits: { contextWindowTokens: 300000 }, reasoning: { kind: "openai-effort", value: "low", defaultValue: "medium", options: [], source: "provider" } })];
  state.metadata = { profiles: state.rows };
  m.registry(true);
  const next = m.resolve("saved:demo");
  assert.equal(next.model, "demo-custom");
  assert.equal(next.reasoning.value, "high");
  assert.equal(next.contextLimits.contextWindowTokens, 300000);
});

test("provider key rotation invalidates every selection and refreshes active credentials", () => {
  const { m, state } = managerWithFake();
  state.rows = [profile({ selection: "builtin:new-provider" })];
  state.metadata = { profiles: state.rows };
  state.resolved = profile({ selection: "builtin:new-provider", apiKey: "sk-old" });
  m.registry();
  m.useProfile("builtin:new-provider");
  assert.equal(m.current.apiKey, "sk-old");
  m.setProviderKey("new-provider", "sk-new");
  assert.equal(m.current.apiKey, "sk-new");
  assert.equal(m.resolve("builtin:new-provider").apiKey, "sk-new");
  assert.ok(state.resolveCalls >= 2, "key rotation must re-resolve cached launch bases");
});

test("wall-clock refresh timestamp remains user-facing while TTL uses monotonic time", () => {
  const { m } = managerWithFake();
  const snap = m.snapshot(true);
  assert.ok(Number.isFinite(snap.catalogRefreshedAt));
  assert.ok(snap.catalogRefreshedAt > 1_000_000_000_000, "refresh label should remain epoch milliseconds");
  assert.ok(m.registryCacheMonotonicAt > 0, "TTL should have a separate monotonic timestamp");
});
