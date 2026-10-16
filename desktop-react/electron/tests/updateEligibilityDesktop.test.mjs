import { test, mock } from "node:test";
import assert from "node:assert/strict";

let remoteAllowed = true;
let channel = "";
mock.module("../../dist-electron/updateAccessDiagnostics.js", { namedExports: {
  recordUpdateAccessDiagnostic() { throw new Error("Desktop must not write Host diagnostics"); },
} });
mock.module("../../dist-electron/hostAccount.js", { namedExports: {
  hostAccount: { async status() { throw new Error("Desktop must never read its local login session"); } },
} });
mock.module("../../dist-electron/hostProcess.js", { namedExports: {
  isHostProcess: false,
  handleHostChannel(name) { channel = name; },
  async callHost(name) {
    assert.equal(name, channel);
    return { allowed: remoteAllowed, reason: remoteAllowed ? "allowed" : "revoked" };
  },
} });
const { mayReceiveUpdates } = await import("../../dist-electron/updateEligibility.js");

test("Desktop update entitlement comes from Host IPC rather than Desktop profile", async () => {
  assert.equal(channel, "loom:update-access");
  assert.equal(await mayReceiveUpdates(), true);
  remoteAllowed = false;
  assert.equal(await mayReceiveUpdates(), false);
});
