import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

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
