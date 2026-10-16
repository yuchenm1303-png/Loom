import { test, mock } from "node:test";
import assert from "node:assert/strict";

let snapshot = { reachable: true, authenticated: true, user: { id: 13, status: "active", updates_enabled: true } };
let delay = 0, calls = 0;
mock.module("../../dist-electron/hostAccount.js", { namedExports: {
  hostAccount: { async status() {
    calls++;
    if (delay) await new Promise(resolve => setTimeout(resolve, delay));
    if (snapshot instanceof Error) throw snapshot;
    return snapshot;
  } },
} });
mock.module("../../dist-electron/hostProcess.js", { namedExports: {
  isHostProcess: true,
  handleHostChannel() {},
  callHost: async () => { throw new Error("Host IPC should not be called in Host tests"); },
} });
const { mayReceiveUpdates } = await import("../../dist-electron/updateEligibility.js");

test("per-user update policy honors server grant, revocation and account changes", async () => {
  assert.equal(await mayReceiveUpdates(), true);
  snapshot = { reachable: true, authenticated: true, user: { id: 13, status: "active", updates_enabled: false } };
  assert.equal(await mayReceiveUpdates(), false);
  snapshot = { reachable: true, authenticated: true, user: { id: 29, status: "active", updates_enabled: true } };
  assert.equal(await mayReceiveUpdates(), true);
  snapshot = { reachable: true, authenticated: true, user: { id: 29, status: "disabled", updates_enabled: true } };
  assert.equal(await mayReceiveUpdates(), false);
});
test("update policy fails closed when signed out, offline, or authentication errors occur", async () => {
  snapshot = { reachable: false, authenticated: true, user: { id: 13, status: "active", updates_enabled: true } };
  assert.equal(await mayReceiveUpdates(), false);
  snapshot = { reachable: true, authenticated: false, user: null };
  assert.equal(await mayReceiveUpdates(), false);
  snapshot = new Error("network unavailable");
  assert.equal(await mayReceiveUpdates(), false);
});

test("slow account verification can finish and concurrent update channels share the request", async () => {
  snapshot = { reachable: true, authenticated: true, user: { id: 13, status: "active", updates_enabled: true } };
  delay = 3200;
  const before = calls;
  try {
    assert.deepEqual(await Promise.all([mayReceiveUpdates(), mayReceiveUpdates()]), [true, true]);
    assert.equal(calls, before + 1);
    delay = 0;
    snapshot.user.updates_enabled = false;
    assert.equal(await mayReceiveUpdates(), false, "a completed grant must not hide later revocation");
  } finally { delay = 0; }
});

test("an explicit short deadline denies activation without cancelling other callers", async () => {
  snapshot = { reachable: true, authenticated: true, user: { id: 13, status: "active", updates_enabled: true } };
  delay = 60;
  try {
    assert.deepEqual(await Promise.all([mayReceiveUpdates(10), mayReceiveUpdates(200)]), [false, true]);
  } finally { delay = 0; }
});
