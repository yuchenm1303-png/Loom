import { test, mock } from "node:test";
import assert from "node:assert/strict";

let snapshot = { reachable: true, authenticated: true, user: { id: 13, status: "active", updates_enabled: true } };
let delay = 0, calls = 0, transientOutages = 0;
const diagnostics = [];
mock.module("../../dist-electron/updateAccessDiagnostics.js", { namedExports: {
  recordUpdateAccessDiagnostic: record => diagnostics.push(record),
} });
mock.module("../../dist-electron/hostAccount.js", { namedExports: {
  hostAccount: { async status() {
    calls++;
    if (transientOutages-- > 0) return { ...snapshot, reachable: false };
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
const { mayReceiveUpdates, verifyUpdateAccess } = await import("../../dist-electron/updateEligibility.js");

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

test("verification preserves the reason instead of treating outages as revocations", async () => {
  snapshot = { configured: true, reachable: false, authenticated: true,
    user: { id: 13, status: "active", updates_enabled: true },
    verificationFailure: { code: "ACCOUNT_REQUEST_TIMEOUT", status: 0 } };
  assert.deepEqual(await verifyUpdateAccess(), { allowed: false, reason: "unreachable", code: "ACCOUNT_REQUEST_TIMEOUT", status: 0 });
  snapshot = { reachable: true, authenticated: true, user: { id: 13, status: "active", updates_enabled: false } };
  assert.deepEqual(await verifyUpdateAccess(), { allowed: false, reason: "revoked" });
  snapshot = { reachable: true, authenticated: false, user: null };
  assert.deepEqual(await verifyUpdateAccess(), { allowed: false, reason: "signed-out" });
  snapshot = { configured: false, reachable: false, authenticated: false, user: null };
  const before = calls;
  assert.deepEqual(await verifyUpdateAccess(), { allowed: false, reason: "unconfigured" });
  assert.equal(calls, before + 1);
});

test("one transient outage retries but explicit revocation does not", async () => {
  snapshot = { reachable: true, authenticated: true, user: { id: 13, status: "active", updates_enabled: true } };
  transientOutages = 1;
  const before = calls;
  assert.deepEqual(await Promise.all([mayReceiveUpdates(), mayReceiveUpdates()]), [true, true]);
  assert.equal(calls, before + 2, "concurrent channels share both attempts");
  snapshot.user.updates_enabled = false;
  const deniedBefore = calls;
  assert.equal(await mayReceiveUpdates(), false);
  assert.equal(calls, deniedBefore + 1, "never retry an explicit denial");
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
    assert.ok(diagnostics.some(record => record.reason === "timeout"));
  } finally { delay = 0; }
});
