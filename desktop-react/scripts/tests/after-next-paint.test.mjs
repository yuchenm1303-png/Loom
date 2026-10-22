import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/state/afterNextPaint.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { afterNextPaint, PAINT_WAIT_LIMIT_MS } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

/** A window whose frames and timers the test drives by hand. */
function fakeWindow({ draws }) {
  const log = [];
  let frameCallback = null;
  const timers = [];
  globalThis.window = {
    requestAnimationFrame(callback) { log.push("frame requested"); if (draws) frameCallback = callback; return 1; },
    setTimeout(callback, ms) { log.push(`timer ${ms}`); timers.push({ callback, ms }); return timers.length; },
  };
  return {
    log,
    frame() { const callback = frameCallback; frameCallback = null; callback?.(); },
    timer(ms) { for (const timer of timers.filter((entry) => entry.ms === ms)) timer.callback(); },
  };
}

test("it resolves after the frame has been drawn, not inside the frame callback", async () => {
  const clock = fakeWindow({ draws: true });
  let resolved = false;
  const waiting = afterNextPaint().then(() => { resolved = true; });
  await Promise.resolve();
  assert.equal(resolved, false, "nothing is drawn yet");
  clock.frame();
  await Promise.resolve();
  assert.equal(resolved, false, "inside the frame callback the frame is still to be drawn");
  clock.timer(0);
  await waiting;
  assert.equal(resolved, true);
});

test("a window that draws nothing does not hold the work for ever", async () => {
  const clock = fakeWindow({ draws: false });
  const waiting = afterNextPaint();
  assert.ok(clock.log.includes(`timer ${PAINT_WAIT_LIMIT_MS}`));
  clock.timer(PAINT_WAIT_LIMIT_MS);
  await waiting;
});

test("resolving twice (the frame and the limit) is harmless", async () => {
  const clock = fakeWindow({ draws: true });
  const waiting = afterNextPaint();
  clock.frame();
  clock.timer(0);
  clock.timer(PAINT_WAIT_LIMIT_MS);
  await waiting;
});

test("opening a conversation that was read before answers the click first", () => {
  const core = readFileSync(new URL("../../src/state/useLoomCore.ts", import.meta.url), "utf8");
  const cached = core.slice(core.indexOf("const cached = cachedThreadRead(normalized);"));
  const apply = cached.indexOf("applyThreadRead(cached);");
  assert.ok(apply > 0);
  const before = cached.slice(0, apply);
  assert.ok(before.includes("await afterNextPaint();"), "the swap waits for the first frame");
  assert.ok(before.indexOf("await afterNextPaint();") < before.indexOf("if (openRequestRef.current !== requestId) return;"), "and is dropped if another conversation was asked for meanwhile");
});
