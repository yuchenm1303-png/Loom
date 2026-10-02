import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/localHostDiscovery.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const model = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("browser authorizes pairing without sending its cookies to the local Host", async () => {
  const original = globalThis.fetch;
  const calls = [];
  try {
    globalThis.fetch = async (url, options) => {
      calls.push([url, options]);
      return { ok: true, json: async () => calls.length === 1 ? { ok: true, pairing_ticket: "one-time-ticket" } : { ok: true } };
    };
    assert.deepEqual(await model.pairLocalLoomHost(), { ok: true, error: "" });
    assert.equal(calls[0][1].credentials, "same-origin");
    assert.equal(calls[1][1].credentials, "omit");
    assert.equal(JSON.parse(calls[1][1].body).pairing_ticket, "one-time-ticket");
    assert.ok(calls[0][1].signal instanceof AbortSignal);
    assert.equal(calls[0][1].signal, calls[1][1].signal);
  } finally { globalThis.fetch = original; }
});

test("canceling discovery or pairing stops the request", async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async (_url, { signal }) => new Promise((_resolve, reject) => {
      if (signal.aborted) reject(new Error("aborted"));
      else signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
    });
    const discovery = new AbortController();
    const pendingDiscovery = model.discoverLocalLoomHost(discovery.signal);
    discovery.abort();
    assert.equal(await pendingDiscovery, null);
    const pairing = new AbortController();
    const pendingPairing = model.pairLocalLoomHost(pairing.signal);
    pairing.abort();
    assert.equal((await pendingPairing).ok, false);
  } finally { globalThis.fetch = original; }
});
