import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/state/startupSequence.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { runStartup } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

const tick = () => new Promise((resolve) => setImmediate(resolve));

/** A launch whose every step the test can hold open and release. */
function harness(overrides = {}) {
  const log = [];
  const gates = { connect: deferred(), models: deferred(), projects: deferred(), threads: deferred(), open: deferred() };
  let cancelled = false;
  const steps = {
    connect: () => { log.push("connect"); return gates.connect.promise; },
    connected: () => log.push("connected"),
    loadModels: () => { log.push("models:start"); return gates.models.promise.finally(() => log.push("models:end")); },
    loadProjects: () => { log.push("projects:start"); return gates.projects.promise.finally(() => log.push("projects:end")); },
    loadThreads: () => { log.push("threads:start"); return gates.threads.promise.finally(() => log.push("threads:end")); },
    ready: () => log.push("ready"),
    openFirst: (thread) => { log.push(`open:${thread.id}`); return gates.open.promise; },
    failed: (cause) => log.push(`failed:${cause instanceof Error ? cause.message : cause}`),
    cancelled: () => cancelled,
    ...overrides,
  };
  return { steps, gates, log, cancel: () => { cancelled = true; } };
}

test("nothing is read before the runtime has answered", async () => {
  const { steps, gates, log } = harness();
  const running = runStartup(steps);
  await tick();
  assert.deepEqual(log, ["connect"]);
  gates.connect.resolve({});
  await tick();
  // The conversation reads are issued first: they are what the window is waiting for.
  assert.deepEqual(log.slice(0, 5), ["connect", "connected", "projects:start", "threads:start", "models:start"]);
  gates.models.resolve(); gates.projects.resolve(); gates.threads.resolve([]);
  await running;
});

test("the window is ready without waiting for the model list", async () => {
  const { steps, gates, log } = harness();
  const running = runStartup(steps);
  gates.connect.resolve({});
  await tick();
  gates.projects.resolve();
  gates.threads.resolve([{ id: "a" }]);
  await tick();

  // The models have not answered, and the conversation list is already usable.
  assert.ok(log.includes("ready"), "ready must not wait for models");
  assert.ok(!log.includes("models:end"));
  assert.ok(log.includes("open:a"));
  gates.open.resolve(); gates.models.resolve();
  await running;
});

test("ready needs both the conversation list and the projects", async () => {
  const { steps, gates, log } = harness();
  const running = runStartup(steps);
  gates.connect.resolve({});
  await tick();
  gates.threads.resolve([]);
  await tick();
  assert.ok(!log.includes("ready"), "projects group the list, so ready waits for them too");
  gates.projects.resolve();
  await tick();
  assert.ok(log.includes("ready"));
  gates.models.resolve();
  await running;
});

test("a model list that fails is still a failed launch, even after the window is ready", async () => {
  const { steps, gates, log } = harness();
  const running = runStartup(steps);
  gates.connect.resolve({});
  await tick();
  gates.projects.resolve();
  gates.threads.resolve([]);
  await running;
  assert.ok(log.includes("ready"));

  gates.models.reject(new Error("registry unavailable"));
  await tick();
  assert.deepEqual(log.filter((entry) => entry.startsWith("failed")), ["failed:registry unavailable"]);
});

test("a runtime that cannot connect fails once and reads nothing", async () => {
  const { steps, gates, log } = harness();
  const running = runStartup(steps);
  gates.connect.reject(new Error("no host"));
  await running;
  assert.deepEqual(log, ["connect", "failed:no host"]);
});

test("a failing conversation list fails the launch", async () => {
  const { steps, gates, log } = harness();
  const running = runStartup(steps);
  gates.connect.resolve({});
  await tick();
  gates.projects.resolve();
  gates.threads.reject(new Error("list failed"));
  await running;
  assert.ok(!log.includes("ready"));
  assert.deepEqual(log.filter((entry) => entry.startsWith("failed")), ["failed:list failed"]);
  gates.models.resolve();
});

test("the most recent conversation opens once the window is ready, and an open that fails fails the launch", async () => {
  const { steps, gates, log } = harness();
  const running = runStartup(steps);
  gates.connect.resolve({});
  await tick();
  gates.projects.resolve();
  gates.threads.resolve([{ id: "newest" }, { id: "older" }]);
  await tick();
  assert.ok(log.indexOf("ready") < log.indexOf("open:newest"));
  assert.ok(!log.includes("open:older"));
  gates.open.reject(new Error("read stalled"));
  await running;
  assert.deepEqual(log.filter((entry) => entry.startsWith("failed")), ["failed:read stalled"]);
  gates.models.resolve();
});

test("with no conversations nothing is opened", async () => {
  const { steps, gates, log } = harness();
  const running = runStartup(steps);
  gates.connect.resolve({});
  await tick();
  gates.projects.resolve();
  gates.threads.resolve([]);
  gates.models.resolve();
  await running;
  assert.ok(!log.some((entry) => entry.startsWith("open:")));
});

test("a launch that was cancelled reports and reads nothing further", async () => {
  const { steps, gates, log, cancel } = harness();
  const running = runStartup(steps);
  cancel();
  gates.connect.resolve({});
  await running;
  assert.deepEqual(log, ["connect"]);
});

test("a model failure after cancellation is not reported", async () => {
  const { steps, gates, log, cancel } = harness();
  const running = runStartup(steps);
  gates.connect.resolve({});
  await tick();
  cancel();
  gates.models.reject(new Error("late"));
  gates.projects.resolve();
  gates.threads.resolve([{ id: "a" }]);
  await running;
  assert.ok(!log.some((entry) => entry.startsWith("failed")));
});

test("the hook runs its launch through runStartup and exposes the stage", () => {
  const hook = readFileSync(new URL("../../src/state/useLoomCore.ts", import.meta.url), "utf8");
  assert.match(hook, /void runStartup<InitializeResult, ThreadRecord>\(/);
  assert.match(hook, /setStartupStage\("library"\)/);
  // The old chain awaited the model list before anything else; it must stay gone.
  assert.ok(!/await refreshModels\(\);\s*\n\s*if \(disposed\) return;\s*\n\s*await refreshProjects\(\)/.test(hook));
});
