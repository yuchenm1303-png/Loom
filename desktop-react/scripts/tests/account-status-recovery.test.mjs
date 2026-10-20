import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

test("offline account recovers without focus, coalesces probes, backs off and cleans up", async () => {
  const source = readFileSync(new URL("../../src/state/useAccount.ts", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  const states = [], effects = [], timers = new Map();
  let now = 0, calls = 0, finish;
  const react = {
    useRef: (value) => ({ current: value }), useCallback: (fn) => fn,
    useEffect: (fn) => effects.push(fn),
    useState(value) { const index = states.push(value) - 1; return [value, (next) => { states[index] = next; }]; },
  };
  const offline = { configured: true, reachable: false, authenticated: true, user: { id: 5 } };
  const window = {
    loom: { accountStatus() { calls++; return new Promise(resolve => { finish = resolve; }); } },
    setInterval(fn) { timers.set(1, fn); return 1; }, clearInterval(id) { timers.delete(id); },
    addEventListener() {}, removeEventListener() {},
  };
  const exports = {};
  new Function("require", "exports", "window", "Date", code)(() => react, exports, window, { now: () => now });
  const account = exports.useAccount();
  const cleanup = effects.at(-1)();
  const first = account.refresh();
  await account.refresh();
  assert.equal(calls, 1);
  finish({ ok: true, snapshot: offline }); await first;
  now = 4_999; timers.get(1)(); assert.equal(calls, 1);
  now = 5_000; timers.get(1)(); timers.get(1)(); assert.equal(calls, 2);
  finish({ ok: true, snapshot: offline }); await new Promise(resolve => setImmediate(resolve));
  now = 14_999; timers.get(1)(); assert.equal(calls, 2);
  now = 15_000; timers.get(1)(); assert.equal(calls, 3);
  finish({ ok: true, snapshot: { ...offline, reachable: true } });
  await new Promise(resolve => setImmediate(resolve));
  now = 100_000; timers.get(1)(); assert.equal(calls, 3);
  assert.equal(states[0].reachable, true);
  assert.equal(states[0].authenticated, true);
  const rejected = account.refresh();
  finish({ ok: false, error: { code: "FORBIDDEN", status: 403, message: "denied" } });
  await rejected;
  now = 200_000; timers.get(1)(); assert.equal(calls, 4);
  cleanup(); assert.equal(timers.size, 0);
});

test("failed initial account probe recovers on focus and ignores refresh during account mutation", async () => {
  const source = readFileSync(new URL("../../src/state/useAccount.ts", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  const states = [], effects = [], listeners = new Map();
  const react = {
    useRef: (value) => ({ current: value }),
    useCallback: (fn) => fn,
    useEffect: (fn) => effects.push(fn),
    useState(value) {
      const index = states.push(value) - 1;
      return [value, (next) => { states[index] = next; }];
    },
  };
  let probes = 0, finishLogin;
  const snapshot = { configured: true, reachable: true, authenticated: true, user: { id: 5 }, serviceUrl: "https://example.test" };
  const window = {
    setInterval: () => 1,
    clearInterval: () => {},
    loom: {
      async accountStatus() {
        if (++probes === 1) throw new Error("Host starting");
        return { ok: true, snapshot };
      },
      accountLogin: () => new Promise((resolve) => { finishLogin = resolve; }),
      onAccountOAuthResult: () => () => {},
    },
    addEventListener: (name, fn) => listeners.set(name, fn),
    removeEventListener: (name) => listeners.delete(name),
  };
  const exports = {};
  new Function("require", "exports", "window", code)(() => react, exports, window);
  const account = exports.useAccount();
  await account.refresh();
  assert.equal(states[4].code, "ACCOUNT_REQUEST_FAILED");
  assert.equal(states[0].configured, false);
  const cleanup = effects.at(-1)();
  listeners.get("focus")();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(states[0], snapshot);
  assert.equal(states[4], null);
  const login = account.login("user@example.test", "password");
  listeners.get("online")();
  assert.equal(probes, 2);
  finishLogin({ ok: true, snapshot });
  await login;
  cleanup();
  assert.equal(listeners.size, 0);
});
