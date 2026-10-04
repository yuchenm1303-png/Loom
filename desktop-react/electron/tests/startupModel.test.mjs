import assert from "node:assert/strict";
import { test } from "node:test";
import { startupModel } from "../../dist-electron/startupModel.js";

const account = { authMode: "loom-account", apiKey: "stale-key", selection: "builtin:ant-ling" };
for (const reason of ["signed out", "network unavailable", "expired account session"]) {
  test(`local startup succeeds when ${reason}`, async () => {
    const result = await startupModel(account, async () => { throw new Error(reason); });
    assert.equal(result.deferred, true);
    assert.equal(result.spec.apiKey, "");
    assert.equal(account.apiKey, "stale-key");
  });
}
test("sign-in materializes a fresh scoped account credential", async () => {
  const result = await startupModel(account, async () => "loom_model_scoped");
  assert.equal(result.deferred, false);
  assert.equal(result.spec.apiKey, "loom_model_scoped");
});
test("local user connection does not depend on account service", async () => {
  const saved = { apiKey: "user-key", selection: "saved:demo" };
  const result = await startupModel(saved, async () => { throw new Error("must not call"); });
  assert.equal(result.spec, saved);
  assert.equal(result.deferred, false);
});
