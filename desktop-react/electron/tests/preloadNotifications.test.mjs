import assert from "node:assert/strict";
import { test } from "node:test";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import vm from "node:vm";
import ts from "typescript";

function harness() {
  const ipc = new EventEmitter();
  const timers = new Set();
  let api;
  const context = {
    exports: {},
    require: () => ({ contextBridge: { exposeInMainWorld: (_name, value) => { api = value; } }, ipcRenderer: ipc }),
    window: { addEventListener() {} },
    setTimeout: fn => { timers.add(fn); return fn; },
    clearTimeout: fn => timers.delete(fn),
  };
  const source = fs.readFileSync(new URL("../preload.cts", import.meta.url), "utf8");
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  } }).outputText, context);
  return {
    api,
    emit: payload => ipc.emit("loom:notification", {}, payload),
    flush: () => { for (const fn of [...timers]) fn(); },
  };
}

const delta = fields => ({ jsonrpc: "2.0", method: "item/delta",
  params: { threadId: "thread", itemId: "assistant", delta: fields } });

test("multiple notification subscribers receive each text fragment exactly once", () => {
  const h = harness();
  const received = [[], [], []];
  const unsubscribe = received.map(messages => h.api.onNotification(message => messages.push(message)));
  const first = delta({ text: "SSH 这次在 banner" });
  h.emit(first);
  h.emit(delta({ text: " 交换阶段就超时了" }));
  h.emit(delta({ text: "（不是 nmap 的结果）" }));
  h.flush();
  for (const messages of received) {
    assert.equal(messages.length, 1);
    assert.equal(messages[0].params.delta.text, "SSH 这次在 banner 交换阶段就超时了（不是 nmap 的结果）");
  }
  assert.equal(first.params.delta.text, "SSH 这次在 banner");
  // A new batch must not change notifications already delivered.
  h.emit(delta({ text: "哈" }));
  h.emit(delta({ text: "哈" }));
  h.flush();
  for (const messages of received) assert.equal(messages[1].params.delta.text, "哈哈");
  unsubscribe.forEach(fn => fn());
  h.emit(delta({ text: "ignored" }));
  h.flush();
  assert.ok(received.every(messages => messages.length === 2));
});

test("batching preserves reasoning and tool JSON fragments before completion", () => {
  const h = harness();
  const messages = [];
  h.api.onNotification(message => messages.push(message));
  h.emit(delta({ reasoning: "先检查", kind: "tool_call_argument", arguments: '{"path":' }));
  h.emit(delta({ reasoning: "再执行", kind: "tool_call_argument", arguments: '"a.txt"}' }));
  h.emit({ jsonrpc: "2.0", method: "item/completed", params: { item: { id: "assistant" } } });
  h.emit({ jsonrpc: "2.0", method: "turn/completed", params: {} });
  assert.equal(messages[0].params.delta.reasoning, "先检查再执行");
  assert.equal(messages[0].params.delta.arguments, '{"path":"a.txt"}');
  assert.equal(messages[1].method, "item/completed");
  assert.equal(messages[2].method, "turn/completed");
  h.flush();
  assert.equal(messages.length, 3);
});
