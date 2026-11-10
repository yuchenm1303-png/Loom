import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import vm from "node:vm";
import ts from "typescript";

test("preload refuses automatic refresh during typing, IME, hidden drafts, attachments, dialogs and unsent text", () => {
  const listeners = new Map(), ipc = new Map();
  let time = 0, blocked = false, text = "", editable = "", result;
  const context = {
    exports: {}, require: () => ({ contextBridge: { exposeInMainWorld() {} },
      ipcRenderer: { on: (key, fn) => ipc.set(key, fn), send: (_channel, _token, safe) => { result = safe; } } }),
    window: { addEventListener: (key, fn) => listeners.set(key, fn), dispatchEvent() {} }, Event,
    document: { querySelector: () => blocked, querySelectorAll: selector => selector === "textarea,input"
      ? [{ value: text, type: "textarea", readOnly: false, disabled: false }] : [{ textContent: editable }] },
    Date: { now: () => time }, setTimeout, clearTimeout,
  };
  const source = fs.readFileSync(new URL("../preload.cts", import.meta.url), "utf8");
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, context);
  const prepare = () => { ipc.get("loom:ui-update-prepare")({}, "nonce"); return result; };
  assert.equal(prepare(), false);
  time = 16_000; assert.equal(prepare(), true);
  text = "unsent"; assert.equal(prepare(), false);
  text = ""; blocked = true; assert.equal(prepare(), false);
  blocked = false; editable = "edit"; assert.equal(prepare(), false);
  editable = ""; listeners.get("compositionstart")(); assert.equal(prepare(), false);
  listeners.get("compositionend")(); assert.equal(prepare(), false);
  time += 16_000; assert.equal(prepare(), true);
  listeners.get("pointerdown")(); assert.equal(prepare(), false);
});
