import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/webBridge.ts", import.meta.url), "utf8")
  .replaceAll("import.meta.env.VITE_LOOM_WEB_SOCKET_URL", '""');
let generation = 0;

async function setup({ deferAccount = false } = {}) {
  const storage = new Map([["loom.web.localDeviceId", "host-a"]]);
  const localStorage = { getItem: (key) => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) };
  const target = new EventTarget();
  const intervals = new Set();
  const sockets = [];
  let releaseAccount;
  let accountCalls = 0;
  const accountGate = deferAccount ? new Promise((resolve) => { releaseAccount = resolve; }) : Promise.resolve();
  globalThis.window = Object.assign(target, {
    location: { href: "https://loom.test/", host: "loom.test", protocol: "https:", reload() {} },
    history: { replaceState() {} }, localStorage,
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    setTimeout, clearTimeout,
    setInterval(fn, ms) { const id = setInterval(fn, ms); intervals.add(id); return id; },
    clearInterval(id) { intervals.delete(id); clearInterval(id); },
  });
  globalThis.document = { documentElement: { dataset: {}, style: {} } };
  globalThis.CustomEvent ??= class extends Event { constructor(type, init) { super(type); this.detail = init.detail; } };
  globalThis.fetch = async () => {
    accountCalls++;
    await accountGate;
    return { json: async () => ({ ok: true, snapshot: { authenticated: true } }) };
  };
  class Socket extends EventTarget {
    static OPEN = 1;
    static CLOSING = 2;
    constructor(url) {
      super(); this.url = url; this.readyState = 0; sockets.push(this);
      queueMicrotask(() => { this.readyState = 1; this.dispatchEvent(new Event("open")); });
    }
    close() { this.readyState = 3; this.dispatchEvent(new Event("close")); }
    message(value) {
      const event = new Event("message"); event.data = JSON.stringify(value); this.dispatchEvent(event);
    }
    send(raw) {
      const message = JSON.parse(raw);
      if (message.type === "invoke") queueMicrotask(() => this.message({ type: "invoke_result", id: message.id, result: "ok" }));
      if (message.type === "get_status") queueMicrotask(() => this.message({ type: "device_status", online: true, selectedDeviceId: new URL(this.url).searchParams.get("device"), devices: [{ id: "host-a" }] }));
    }
  }
  globalThis.WebSocket = Socket;
  const compiled = ts.transpileModule(source + `\n// test instance ${generation++}`, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const bridge = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
  bridge.installWebBridge();
  return { bridge, sockets, storage, intervals, releaseAccount,
    get accountCalls() { return accountCalls; },
    async close() { await window.loom.disconnect(); } };
}

test("concurrent calls share account lookup and the same selected Host socket", async () => {
  const env = await setup({ deferAccount: true });
  try {
    const first = window.loom.call("one");
    const second = window.loom.call("two");
    assert.equal(env.accountCalls, 1);
    env.releaseAccount();
    assert.deepEqual(await Promise.all([first, second]), ["ok", "ok"]);
    assert.equal(env.sockets.length, 1);
    assert.equal(new URL(env.sockets[0].url).searchParams.get("device"), "host-a");
    assert.equal(env.intervals.size, 1);
  } finally { await env.close(); }
  assert.equal(env.intervals.size, 0);
});

test("disconnect during account lookup cancels a stale connection", async () => {
  const env = await setup({ deferAccount: true });
  const pending = window.loom.call("one");
  await env.close();
  env.releaseAccount();
  await assert.rejects(pending, /cancelled/);
  assert.equal(env.sockets.length, 0);
});

test("local discovery changing the device reconnects and old notifications are ignored", async () => {
  const env = await setup();
  try {
    const notices = [];
    window.loom.onNotification((notice) => notices.push(notice));
    await window.loom.call("one");
    const old = env.sockets[0];
    env.storage.set("loom.web.localDeviceId", "host-b");
    await window.loom.call("two");
    assert.equal(env.sockets.length, 2);
    assert.equal(new URL(env.sockets[1].url).searchParams.get("device"), "host-b");
    old.message({ type: "notification", payload: { method: "stale" } });
    env.sockets[1].message({ type: "notification", payload: { method: "current" } });
    assert.deepEqual(notices, [{ method: "current" }]);
    assert.equal(env.intervals.size, 1);
  } finally { await env.close(); }
});

test("refresh requests a fresh device status rather than returning the cached snapshot", async () => {
  const env = await setup();
  try {
    assert.equal((await env.bridge.getWebDeviceStatus()).selectedDeviceId, "host-a");
    env.sockets[0].message({ type: "device_status", online: false });
    assert.equal((await env.bridge.getWebDeviceStatus()).online, true);
  } finally { await env.close(); }
});
