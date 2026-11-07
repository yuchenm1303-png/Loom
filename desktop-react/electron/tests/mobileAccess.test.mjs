import assert from "node:assert/strict";
import { test } from "node:test";
import { MOBILE_OPERATIONS, MOBILE_RPC_METHODS, mobileInvokeAllowed } from "../../dist-electron/mobileAccess.js";

test("a phone reaches the conversation features", () => {
  for (const method of ["thread/read", "turn/start", "turn/steer", "turn/interrupt", "approval/respond", "thread/set_permission_mode"]) {
    assert.equal(mobileInvokeAllowed("call", [method, {}]), true, method);
  }
  assert.equal(mobileInvokeAllowed("listModels", []), true);
  assert.equal(mobileInvokeAllowed("switchCurrentModel", ["model"]), true);
});

test("keys, clipboard and account features stay on the computer", () => {
  for (const operation of [
    "setModelProviderKey", "addModel", "updateModel", "deleteModel", "discoverModels",
    "readClipboardText", "writeClipboardText", "exportComputerLogs", "exportBrowserLogs",
    "openExternal", "revealPath", "pickDirectory", "setupBrowserExtension", "bootstrapUpdateEnsure",
  ]) {
    assert.equal(mobileInvokeAllowed(operation, []), false, operation);
  }
});

test("call is filtered by method, not waved through", () => {
  for (const method of ["settings/set", "mcp/manage", "plugin/manage", "terminal/session/write", "project/remove", "", undefined]) {
    assert.equal(mobileInvokeAllowed("call", [method, {}]), false, String(method));
  }
  assert.equal(mobileInvokeAllowed("call", []), false);
});

test("the lists hold no wildcard or empty entry", () => {
  for (const entry of [...MOBILE_OPERATIONS, ...MOBILE_RPC_METHODS]) assert.match(entry, /^[a-zA-Z_/]+$/);
});
