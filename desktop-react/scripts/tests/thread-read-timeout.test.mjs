import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/state/threadReadTimeout.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { withThreadReadTimeout, THREAD_READ_TIMEOUT_MS } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("stalled read times out and a late response cannot apply a transcript", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let reply;
  let applied = false;
  const request = withThreadReadTimeout(new Promise(resolve => { reply = resolve; }));
  const result = request.then(() => { applied = true; });
  const rejected = assert.rejects(result, /对话加载超时/);
  t.mock.timers.tick(THREAD_READ_TIMEOUT_MS);
  await rejected;
  reply({ thread: "late" });
  await Promise.resolve();
  assert.equal(applied, false);
  assert.equal(await withThreadReadTimeout(Promise.resolve("retry")), "retry");
});

test("successful and failed reads settle immediately without waiting for timeout", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  assert.equal(await withThreadReadTimeout(Promise.resolve("loaded")), "loaded");
  const failure = new Error("Host disconnected");
  await assert.rejects(withThreadReadTimeout(Promise.reject(failure)), error => error === failure);
  t.mock.timers.tick(THREAD_READ_TIMEOUT_MS);
});
