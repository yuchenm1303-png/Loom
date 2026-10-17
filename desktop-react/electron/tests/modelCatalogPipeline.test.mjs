import assert from "node:assert/strict";
import { mock, test } from "node:test";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createInterface } from "node:readline";

mock.module("electron", { namedExports: { app: { isPackaged: false, isReady: () => false, getPath: () => os.tmpdir() },
  safeStorage: { isEncryptionAvailable: () => false } } });
const { LoomAccountClient } = await import("../../dist-electron/accountClient.js");
const { DesktopModelManager } = await import("../../dist-electron/modelManager.js");
const { applyModelPolicy } = await import("../../dist-electron/modelAccess.js");
const { builtinBaseUrl } = await import("../../dist-electron/modelConnection.js");

test("upstream discovery reaches the Desktop picker while another provider is invalid", { timeout: 25000 }, async () => {
  const home = await mkdtemp(path.join(os.tmpdir(), "loom-catalog-pipeline-"));
  const server = spawn(process.env.LOOM_TEST_PYTHON || "python", [fileURLToPath(new URL("./fixtures/modelCatalogServer.py", import.meta.url)), home],
    { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  let stderr = "";
  server.stderr.on("data", data => { stderr += data; });
  const lines = createInterface({ input: server.stdout });
  try {
    const port = await new Promise((resolve, reject) => {
      lines.once("line", line => { try { resolve(JSON.parse(line).port); } catch (error) { reject(error); } });
      server.once("error", reject);
      server.once("exit", code => reject(Error(`Catalog fixture exited ${code}: ${stderr}`)));
    });
    const base = `http://127.0.0.1:${port}`;
    const client = new LoomAccountClient();
    client.memorySession = { accessToken: "fixture-only", refreshToken: "fixture-only", expiresAt: Date.now() + 3600000,
      user: { id: 7, email: "fixture@example.test", status: "active" } };
    client.modelPolicyAccessUrl = () => base + "/v1/access";
    const manager = new DesktopModelManager(home);
    const profile = { selection: "builtin:opencode-go:omen-alpha", id: "omen-alpha", model: "omen-alpha", name: "Omen Alpha",
      kind: "builtin", authMode: "loom-account", adapter: "opencode-go", baseUrl: builtinBaseUrl("builtin:opencode-go:omen-alpha"), configured: true, available: true };
    manager.adoptRegistry({ profiles: [profile], primary: profile, activeModelId: profile.id }, { profiles: [] });
    // The runtime's metadata bridge is isolated; discovery/policy/account use real HTTP and SQLite.
    manager.runPythonBridgeAsync = async (_script, command, payload) => command === "record-image-capabilities" ? {} : payload.models.map(item => ({
      ...profile, selection: item.selection, id: item.selection, model: item.model, name: item.model, baseUrl: builtinBaseUrl(item.selection) }));
    const initial = await client.modelPolicyAccess(true);
    assert.ok(!initial.catalog.models.some(row => row.model === "beam-free"));
    const before = await manager.applyServerCatalog(manager.snapshot(), initial.catalog.models);
    const consoleCount = before.profiles.filter(row => row.groupId === "opencode-console").length;
    assert.ok(consoleCount > 0);
    assert.ok((await fetch(base + "/fixture/advance", { method: "POST" })).ok);
    const next = await client.modelPolicyAccess(true);
    const picker = applyModelPolicy(await manager.applyServerCatalog(before, next.catalog.models), next);
    const beam = picker.profiles.find(row => row.model === "beam-free");
    assert.ok(beam, "a new model is visible without changing local bundled definitions");
    assert.equal(beam.groupId, "opencode-go");
    assert.equal(beam.available, true);
    assert.equal(picker.profiles.filter(row => row.groupId === "opencode-console").length, consoleCount);
    assert.notEqual(picker.catalogSync.providers.find(row => row.groupId === "opencode-console").state, "synced");
    const consoleSelection = picker.profiles.find(row => row.groupId === "opencode-console").selection;
    const checked = await client.modelPolicyCheck(consoleSelection);
    assert.equal(typeof checked.decisions.find(row => row.model_id === consoleSelection).enabled, "boolean",
      "a preserved display decision must be rechecked against the server, not reused as a fresh grant");
    const launch = await manager.resolveAsync(beam.selection);
    assert.equal(launch.authMode, "loom-account");
    assert.equal(launch.apiKey, "");
  } finally {
    lines.close();
    const stopped = new Promise(resolve => server.exitCode !== null ? resolve() : server.once("exit", resolve));
    server.kill();
    await stopped;
    await rm(home, { recursive: true, force: true });
  }
});
