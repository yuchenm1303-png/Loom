import assert from "node:assert/strict";
import { test } from "node:test";
import { applyModelPolicy, policyAllowsSelection } from "../../dist-electron/modelAccess.js";
import { assertModelConnection, builtinBaseUrl } from "../../dist-electron/modelConnection.js";
import { modelPolicyGroup } from "../../dist-electron/modelPolicySelection.js";

const selections = ["builtin:intern:intern-s2", "builtin:minimax", "builtin:deepseek", "builtin:ant-ling",
  "builtin:opencode-go:deepseek-flash", "managed:relay:model"];
const builtin = selection => ({ selection, kind: "builtin", authMode: "loom-account", available: true,
  model: "deepseek-flash", adapter: "openai-compatible", baseUrl: builtinBaseUrl(selection), apiKey: "" });
const personal = { selection: "profile:m123456789abc", kind: "saved", model: "deepseek-flash",
  baseUrl: "https://personal.example/v1", apiKey: "personal-key", available: true };
const snapshot = profile => ({ profiles: [profile, personal], primary: profile, current: profile });
const policy = (selection, enabled, source = "global") => ({ enabled: true, schema_version: 2, models: enabled ? [selection] : [],
  decisions: [{ model_id: selection, enabled, source }] });

test("same model name does not change connection identity, credentials or policy scope", () => {
  assertModelConnection(personal);
  for (const selection of selections) {
    const profile = builtin(selection);
    assertModelConnection(profile);
    assert.equal(policyAllowsSelection(policy(selection, false), personal.selection), true);
    assert.equal(modelPolicyGroup(personal.selection), null);
    assert.throws(() => assertModelConnection({ ...personal, authMode: "loom-account" }), /Personal/);
    assert.throws(() => assertModelConnection({ ...profile, baseUrl: personal.baseUrl }), /identity/);
    assert.throws(() => assertModelConnection({ ...profile, kind: "saved" }), /identity/);
  }
  assert.throws(() => assertModelConnection({ ...personal, apiKey: "loom_model_accidentally_inherited" }), /Personal/);
});

test("policy outages are unknown, never a catalog retirement or admin denial", () => {
  for (const selection of selections) {
    const profile = builtin(selection);
    const view = applyModelPolicy(snapshot(profile), null);
    assert.equal(view.profiles[0].available, true);
    assert.equal(view.profiles[0].accessState, "unknown");
    assert.equal(view.profiles[1].accessState, "personal");
    assert.equal(profile.accessState, undefined, "projection never mutates catalog state");
  }
});

test("denial, recovery and directory retirement remain separate and projections are reversible", () => {
  const profile = builtin("builtin:deepseek");
  const denied = applyModelPolicy(snapshot(profile), policy(profile.selection, false));
  assert.equal(denied.current.available, false);
  assert.equal(denied.current.catalogAvailable, true);
  assert.equal(denied.current.accessState, "denied");
  const recovered = applyModelPolicy(denied, policy(profile.selection, true));
  assert.equal(recovered.current.available, true);
  assert.equal(recovered.current.statusMessage, undefined);
  const retired = applyModelPolicy(snapshot({ ...profile, available: false, catalogState: "retired" }), policy(profile.selection, true));
  assert.equal(retired.current.available, false, "admin allow cannot resurrect a retired provider model");
  const pending = applyModelPolicy(snapshot({ ...profile, catalogState: "pending-removal" }), policy(profile.selection, true));
  assert.equal(pending.current.available, true, "one incomplete observation is not retirement");
  assert.equal(recovered.profiles[1].baseUrl, personal.baseUrl);
});

test("fresh schema-2 unknown models are denied without inventing a new transport", () => {
  const profile = builtin("builtin:deepseek:new-model");
  const access = { enabled: true, schema_version: 2, models: [], decisions: [] };
  const result = applyModelPolicy(snapshot(profile), access);
  assert.equal(result.current.available, false);
  assert.equal(result.current.baseUrl, profile.baseUrl);
  assert.equal(result.profiles[1].available, true);
});
