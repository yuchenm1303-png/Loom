import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import ts from "typescript";

const source = readFileSync(new URL("../../src/state/modelSwitchRouting.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const exports = {};
runInNewContext(outputText, { exports });
const { switchModelProfileForThread, switchCurrentModelForThread } = exports;

function fakeBridge() {
  const calls = [];
  return {
    calls,
    async switchModelProfile(...args) {
      calls.push(["profile", ...args]);
      return { thread: args.length > 1 ? { id: args[0], modelSelection: args[1] } : null };
    },
    async switchCurrentModel(...args) {
      calls.push(["custom", ...args]);
      return { thread: args.length > 1 ? { id: args[0], modelSelection: args[1], model: args[2] } : null };
    },
  };
}

test("old conversation switches only its thread-scoped profile", async () => {
  const bridge = fakeBridge();
  const result = await switchModelProfileForThread(bridge, "thread-legacy", "builtin:deepseek");
  assert.deepEqual(bridge.calls, [["profile", "thread-legacy", "builtin:deepseek"]]);
  assert.equal(result.thread.id, "thread-legacy");
});

test("old conversation switches its model ID with its thread and selection", async () => {
  const bridge = fakeBridge();
  const result = await switchCurrentModelForThread(bridge, "thread-legacy", "saved:abc", "new-model");
  assert.deepEqual(bridge.calls, [["custom", "thread-legacy", "saved:abc", "new-model"]]);
  assert.equal(result.thread.model, "new-model");
});

test("new conversation keeps the default-model behavior", async () => {
  const bridge = fakeBridge();
  await switchModelProfileForThread(bridge, undefined, "builtin:minimax");
  await switchCurrentModelForThread(bridge, undefined, "", "MiniMax-M3");
  assert.deepEqual(bridge.calls, [["profile", "builtin:minimax"], ["custom", "MiniMax-M3"]]);
});

test("missing legacy model selection is an explicit error, not a silent no-op", async () => {
  const bridge = fakeBridge();
  await assert.rejects(async () => switchCurrentModelForThread(bridge, "thread-legacy", "", "new-model"), /No model profile/);
  assert.equal(bridge.calls.length, 0);
});

test("Host failures propagate to the picker without reporting success", async () => {
  const bridge = {
    switchModelProfile: async () => { throw new Error("finish or stop this thread's active turn"); },
  };
  await assert.rejects(
    switchModelProfileForThread(bridge, "thread-legacy", "builtin:minimax"),
    /finish or stop/,
  );
});
