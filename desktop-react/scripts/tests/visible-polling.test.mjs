import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";
const compiled = ts.transpileModule(readFileSync(new URL("../../src/visiblePolling.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { startVisiblePolling } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("hidden windows pause reads, slow reads never overlap, disposal prevents rescheduling", async () => {
  const originalDocument = globalThis.document;
  const originalSet = globalThis.setTimeout, originalClear = globalThis.clearTimeout;
  const timers = new Map();
  const document = new EventTarget(); document.visibilityState = "visible";
  globalThis.document = document;
  let nextTimer = 0, calls = 0, finish;
  globalThis.setTimeout = callback => { timers.set(++nextTimer, callback); return nextTimer; };
  globalThis.clearTimeout = id => timers.delete(id);
  const visibility = state => { document.visibilityState = state; document.dispatchEvent(new Event("visibilitychange")); };
  const settle = async () => { await Promise.resolve(); await Promise.resolve(); };
  let stop;
  try {
    stop = startVisiblePolling(() => { calls++; return new Promise(resolve => { finish = resolve; }); }, 12000);
    assert.equal(calls, 1);
    visibility("hidden"); visibility("visible"); visibility("visible");
    assert.equal(calls, 1, "focus cannot duplicate an unfinished repository read");
    assert.equal(timers.size, 0);
    finish(); await settle(); assert.equal(timers.size, 1);
    visibility("hidden"); assert.equal(timers.size, 0);
    visibility("visible"); assert.equal(calls, 2);
    stop(); finish(); await settle();
    assert.equal(timers.size, 0);
    visibility("hidden"); visibility("visible"); assert.equal(calls, 2);
    document.visibilityState = "hidden";
    stop = startVisiblePolling(async () => { calls++; }, 12000);
    assert.equal(calls, 2, "mounting in a minimized window performs no read");
    visibility("visible"); await settle(); assert.equal(calls, 3);
    const callback = [...timers.values()][0]; timers.clear(); callback(); await settle();
    assert.equal(calls, 4, "visible windows continue periodic refresh");
    stop(); assert.equal(timers.size, 0);
    stop = startVisiblePolling(async () => { throw new Error("offline"); }, 12000);
    await settle(); assert.equal(timers.size, 1, "temporary errors do not stop future refreshes");
  } finally {
    stop?.(); globalThis.document = originalDocument;
    globalThis.setTimeout = originalSet; globalThis.clearTimeout = originalClear;
  }
});
