import assert from "node:assert/strict";
import { mock, test } from "node:test";
import os from "node:os";
mock.module("electron", { namedExports: { app: {
  isPackaged: false, isReady: () => false, getPath: () => os.tmpdir(),
} } });
const { DesktopModelManager } = await import("../../dist-electron/modelManager.js");

test("one unsupported server model cannot fail startup or discard compatible rows", async () => {
  const manager = new DesktopModelManager(os.tmpdir());
  const known = profile({ selection: "builtin:ant-ling", model: "Ling-3.0-flash", kind: "builtin" });
  const saved = profile();
  const models = [
    { model_id: known.selection, model: known.model, name: "Known", group_id: "ant-ling", group_name: "Ant Ling", available: true },
    { model_id: "builtin:ant-ling:New", model: "New", name: "New", group_id: "ant-ling", group_name: "Ant Ling", available: true },
    { model_id: "builtin:ant-ling:AntAngelMed", model: "AntAngelMed", name: "AntAngelMed", group_id: "ant-ling", group_name: "Ant Ling", available: true },
  ];
  let repaired = false;
  manager.runPythonBridgeAsync = async (_script, command, payload) => {
    if (command === "describe-models") {
      if (!repaired) throw new Error("unsupported Ant Ling built-in model id: 'AntAngelMed'");
      return payload.models.map(row => profile({ selection: row.selection, model: row.model, kind: "builtin" }));
    }
    if (payload.model === "AntAngelMed") throw new Error("unsupported model");
    return profile({ selection: payload.selection, model: payload.model, kind: "builtin" });
  };
  const snapshot = { ...registry([known, saved]), current: null, recentModels: [] };
  const result = await manager.applyServerCatalog(snapshot, models);
  assert.equal(result.profiles.length, 4);
  assert.equal(result.profiles[0].available, true);
  assert.equal(result.profiles[1].available, true);
  assert.equal(result.profiles[2].available, false);
  assert.equal(result.profiles[2].configured, false);
  assert.equal(result.profiles[2].baseUrl, "");
  assert.match(result.profiles[2].statusMessage, /Host runtime/);
  assert.equal(result.profiles[3].selection, saved.selection);
  repaired = true;
  const retry = await manager.applyServerCatalog(snapshot, models);
  assert.equal(retry.profiles[2].available, true, "metadata failures are not cached as permanent exclusions");
});

function profile(overrides = {}) {
  return {
    selection: "saved:demo",
    id: "saved-demo",
    kind: "saved",
    name: "Demo",
    adapter: "openai-compatible",
    baseUrl: "https://api.example.com/v1",
    model: "demo-1",
    provider: "demo",
    apiKey: "sk-old",
    groupId: "demo",
    groupName: "Demo",
    configured: true,
    available: true,
    catalogSource: "provider",
    vision: true,
    contextLimits: { contextWindowTokens: 200_000 },
    reasoning: { kind: "openai-effort", value: "medium", defaultValue: "medium", options: [], source: "provider" },
    ...overrides,
  };
}

function registry(rows = [profile()]) {
  return { primary: rows[0] ?? profile(), profiles: rows, activeModelId: rows[0]?.id ?? null };
}

test("clean install selects account model without resolving a provider key", () => {
  const manager = new DesktopModelManager(os.tmpdir());
  const minimax = profile({ id: "minimax-primary", selection: "builtin:minimax", configured: false });
  const account = profile({ id: "account", selection: "builtin:ant-ling", authMode: "loom-account" });
  manager.registry = () => ({ primary: minimax, profiles: [minimax, account], activeModelId: minimax.id });
  manager.resolve = (selection) => {
    assert.equal(selection, "builtin:ant-ling");
    return { ...account, apiKey: "", provider: "openai-compatible" };
  };
  assert.equal(manager.ensureInitial().authMode, "loom-account");
});

test("configured user connection remains the startup selection", () => {
  const manager = new DesktopModelManager(os.tmpdir());
  manager.registry = () => registry();
  manager.resolve = (selection) => { assert.equal(selection, "saved:demo"); return profile(); };
  assert.equal(manager.ensureInitial().selection, "saved:demo");
});

test("legacy default without configured metadata recovers from missing key", () => {
  const manager = new DesktopModelManager(os.tmpdir());
  const minimax = profile({ id: "minimax-primary", selection: "builtin:minimax", configured: undefined });
  const account = profile({ id: "account", selection: "builtin:ant-ling", authMode: "loom-account" });
  manager.registry = () => ({ primary: minimax, profiles: [minimax, account], activeModelId: minimax.id });
  manager.resolve = (selection) => {
    if (selection === minimax.selection) throw new Error("MiniMax API key is not configured.");
    return { ...account, apiKey: "", provider: "openai-compatible" };
  };
  assert.equal(manager.ensureInitial().selection, account.selection);
});

function installFakeBridge(manager) {
  const state = {
    registry: registry(),
    metadata: { profiles: [profile()] },
    resolved: profile(),
    calls: [],
  };

  manager.runPythonBridge = (script, command, payload = {}) => {
    state.calls.push(`${script}:${command}`);
    if (script === "loom_ant_ling_bridge.py" && command === "list") {
      return { profiles: [], activeSelection: null };
    }
    if (command === "list") return state.registry;
    if (command === "resolve") return { ...state.resolved, selection: payload.selection ?? state.resolved.selection };
    if (command === "describe-model") {
      return { ...state.resolved, selection: payload.selection ?? state.resolved.selection, model: payload.model };
    }
    if (command === "set-reasoning") {
      return profile({
        reasoning: { kind: payload.kind, value: payload.value, defaultValue: "medium", options: [], source: "user" },
      });
    }
    if (command === "set-provider-key") {
      state.resolved = { ...state.resolved, apiKey: payload.apiKey };
      return { provider: payload.provider, configured: true };
    }
    throw new Error(`unexpected bridge call ${script}:${command}`);
  };

  manager.runAdmin = (command) => {
    state.calls.push(`loom_model_admin.py:${command}`);
    if (command === "metadata") return state.metadata;
    throw new Error(`unexpected admin call ${command}`);
  };

  return state;
}

function makeManager() {
  const previous = process.env.LOOM_MODEL_CATALOG_TTL_MS;
  process.env.LOOM_MODEL_CATALOG_TTL_MS = "30000";
  try {
    return new DesktopModelManager("/tmp/loom-model-cache-test");
  } finally {
    if (previous === undefined) delete process.env.LOOM_MODEL_CATALOG_TTL_MS;
    else process.env.LOOM_MODEL_CATALOG_TTL_MS = previous;
  }
}

function resolveCalls(state) {
  return state.calls.filter((value) => value === "loom_model_bridge.py:resolve").length;
}

test("catalog refresh reprojects dynamic fields without re-resolving", () => {
  const manager = makeManager();
  const state = installFakeBridge(manager);
  manager.registry(true);
  const before = manager.resolve("saved:demo");
  assert.equal(before.contextLimits.contextWindowTokens, 200_000);
  assert.equal(resolveCalls(state), 1);

  state.registry = registry([profile({
    available: false,
    contextLimits: { contextWindowTokens: 400_000 },
  })]);
  manager.registry(true);

  const after = manager.resolve("saved:demo");
  assert.equal(after.available, false);
  assert.equal(after.contextLimits.contextWindowTokens, 400_000);
  assert.equal(resolveCalls(state), 1, "refresh must stay in-memory for surviving selections");
});

test("current spec is refreshed together with the cached selection", () => {
  const manager = makeManager();
  const state = installFakeBridge(manager);
  manager.registry(true);
  manager.useProfile("saved:demo");

  state.registry = registry([profile({ contextLimits: { contextWindowTokens: 512_000 } })]);
  manager.registry(true);

  assert.equal(manager.current.contextLimits.contextWindowTokens, 512_000);
});

test("explicit model override survives catalog refresh", () => {
  const manager = makeManager();
  const state = installFakeBridge(manager);
  manager.registry(true);
  manager.useProfile("saved:demo");
  manager.useModelName("demo-custom");

  state.registry = registry([profile({ model: "demo-provider-default", contextLimits: { contextWindowTokens: 300_000 } })]);
  manager.registry(true);

  assert.equal(manager.current.model, "demo-custom");
  assert.equal(manager.current.contextLimits.contextWindowTokens, 300_000);
});

test("explicit reasoning override survives catalog refresh", () => {
  const manager = makeManager();
  const state = installFakeBridge(manager);
  manager.registry(true);
  manager.useProfile("saved:demo");
  manager.setReasoning("openai-effort", "high");

  state.registry = registry([profile({
    reasoning: { kind: "openai-effort", value: "low", defaultValue: "low", options: [], source: "provider" },
  })]);
  manager.registry(true);

  assert.equal(manager.current.reasoning.value, "high");
});

test("sync registry path refreshes after TTL expiry", () => {
  const manager = makeManager();
  const state = installFakeBridge(manager);
  manager.registry();
  const firstLists = state.calls.filter((value) => value === "loom_model_bridge.py:list").length;
  manager.registry();
  assert.equal(state.calls.filter((value) => value === "loom_model_bridge.py:list").length, firstLists);

  manager.registryCacheMonotonicAt = performance.now() - 31_000;
  state.registry = registry([profile({ model: "demo-2" })]);
  assert.equal(manager.registry().profiles[0].model, "demo-2");
  assert.equal(state.calls.filter((value) => value === "loom_model_bridge.py:list").length, firstLists + 1);
});

test("provider-key change clears launch cache and re-resolves current model", () => {
  const manager = makeManager();
  const state = installFakeBridge(manager);
  manager.registry(true);
  manager.useProfile("saved:demo");
  assert.equal(manager.current.apiKey, "sk-old");
  const before = resolveCalls(state);

  manager.setProviderKey("demo", "sk-new");

  assert.equal(manager.current.apiKey, "sk-new");
  assert.equal(resolveCalls(state), before + 1);
});

test("saving a connection uses async bridges and preserves the provider catalogue", async () => {
  const manager = makeManager();
  const state = installFakeBridge(manager);
  manager.registry(true);
  const before = [...state.calls];
  const added = profile({ selection: "saved:new", id: "new", name: "New", catalogSource: "saved", vision: false });
  const calls = [];
  manager.runBridgeAsync = async (command) => {
    calls.push(command);
    if (command === "save") return added;
    if (command === "resolve") return { ...added, apiKey: "test-secret" };
    throw new Error(`unexpected catalogue reload: ${command}`);
  };
  const saved = await manager.add({ name: "New", adapter: "openai-compatible", baseUrl: added.baseUrl, model: added.model, apiKey: "test-secret", vision: false });
  const spec = manager.resolve(saved.selection);
  const snapshot = manager.snapshotFor(spec);
  assert.deepEqual(calls, ["save", "resolve"]);
  assert.deepEqual(state.calls, before, "save and switch must not start synchronous Python bridges");
  assert.equal(snapshot.profiles.length, 2);
  assert.equal(spec.vision, false);
  assert.equal(spec.apiKey, "test-secret");
  assert.equal(JSON.stringify(snapshot).includes("test-secret"), false);
});

test("failed async save leaves the cached catalogue intact", async () => {
  const manager = makeManager();
  const state = installFakeBridge(manager);
  manager.registry(true);
  manager.runBridgeAsync = async () => { throw new Error("credential store unavailable"); };
  await assert.rejects(manager.add({ name: "New", adapter: "openai", baseUrl: "", model: "demo", apiKey: "test-secret" }), /credential store unavailable/);
  assert.deepEqual(manager.snapshot().profiles, state.registry.profiles);
});
