import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const core = readFileSync(new URL("../../src/state/useLoomCore.ts", import.meta.url), "utf8");
const app = readFileSync(new URL("../../src/App.tsx", import.meta.url), "utf8");
const source = ts.createSourceFile("core.ts", core, ts.ScriptTarget.ES2022, true);
function callback(name) {
  let result;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(source) === name) result = node.initializer.arguments[0].getText(source);
    ts.forEachChild(node, visit);
  }
  visit(source);
  assert.ok(result, name);
  return ts.transpileModule(`const callback = ${result};`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
}

test("draft permission updates immediately without RPC and preserves project/workspace", async () => {
  for (const params of [{ projectId: "project" }, { workspace: "C:/work" }]) {
    const ref = { current: params };
    const states = [];
    const update = new Function("active", "draftThreadParamsRef", "setDraftPermissionMode", "requireBridge",
      `${callback("setPermissionMode")} return callback;`)(null, ref, value => states.push(value), () => { throw new Error("Draft must not issue RPC"); });
    await update("read-only");
    await update("full-access");
    assert.deepEqual(ref.current, { ...params, permissionMode: "full-access" });
    assert.deepEqual(states, ["read-only", "full-access"]);
  }
  assert.match(app, /thread\?\.permissionMode \|\| loom\.draftPermissionMode \|\| loom\.runtime\.defaultPermissionMode/);
  assert.match(core, /"thread\/start", draftThreadParamsRef\.current/);
  assert.match(core, /draftThreadParamsRef\.current = \{\};\s*setDraftPermissionMode\(undefined\)/);
});

test("existing conversation updates server permission and archived conversation stays unchanged", async () => {
  const calls = [];
  const updated = { id: "thread", permissionMode: "workspace" };
  const update = new Function("active", "draftThreadParamsRef", "setDraftPermissionMode", "requireBridge", "setThreads", "setActive",
    `${callback("setPermissionMode")} return callback;`)(
      { thread: { id: "thread" } }, { current: {} }, () => { throw new Error("Must not change draft"); },
      () => ({ call: async (...args) => { calls.push(args); return { thread: updated }; } }),
      apply => assert.deepEqual(apply([{ id: "thread" }]), [updated]),
      apply => assert.deepEqual(apply({ thread: { id: "thread" } }), { thread: updated }));
  await update("workspace");
  assert.deepEqual(calls, [["thread/set_permission_mode", { threadId: "thread", permissionMode: "workspace" }]]);
  const archived = new Function("active", `${callback("setPermissionMode")} return callback;`)({ thread: { id: "thread", archived: true } });
  await archived("full-access");
});
