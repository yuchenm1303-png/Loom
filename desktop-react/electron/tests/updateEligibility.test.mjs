import { test, mock } from "node:test";
import assert from "node:assert/strict";

let snapshot = { reachable: true, authenticated: true, user: { id: 13, status: "active", updates_enabled: true } };
mock.module("../../dist-electron/hostAccount.js", { namedExports: {
  hostAccount: { async status() {
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
