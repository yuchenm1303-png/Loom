import assert from "node:assert/strict";
import { test } from "node:test";
import { randomUUID } from "node:crypto";
import os from "node:os";
import path from "node:path";
import { HostClient, HostServer } from "../../dist-electron/hostTransport.js";

function endpoint() {
  const id = randomUUID();
  return process.platform === "win32" ? `\\\\.\\pipe\\loom-test-${id}` : path.join(os.tmpdir(), `loom-${id}.sock`);
}

test("one Host serves desktop and other clients; closing desktop keeps work alive", async () => {
  const address = endpoint();
  let taskCount = 0;
  const server = new HostServer("secret", new Map([
    ["task", async ([bytes]) => { taskCount++; return bytes; }],
    ["status", async () => taskCount],
    ["fail", async () => { throw new Error("Expected operation error"); }],
  ]));
  const events = [];
  const desktop = new HostClient((channel, value) => events.push([channel, value]));
  const web = new HostClient(() => {});
  try {
    await server.listen(address);
    await Promise.all([desktop.connect(address, "secret"), web.connect(address, "secret")]);
    const bytes = new Uint8Array([0, 255, 42]);
    assert.deepEqual(await desktop.call("task", [bytes]), bytes);
    await assert.rejects(desktop.call("fail", []), /Expected operation error/);
    await assert.rejects(desktop.call("not-registered", []), /Unknown Host operation/);
    server.broadcast("loom:notification", { method: "item/delta", params: { text: "hello" } });
    await desktop.call("status", []);
    assert.deepEqual(events[0], ["loom:notification", { method: "item/delta", params: { text: "hello" } }]);
    desktop.close();
    assert.equal(await web.call("status", []), 1);
    await web.call("task", [bytes]);
    await desktop.connect(address, "secret");
    assert.equal(await desktop.call("status", []), 2);
  } finally { desktop.close(); web.close(); server.close(); }
});

test("wrong credential cannot invoke Host operations", async () => {
  const address = endpoint();
  const server = new HostServer("secret", new Map());
  const client = new HostClient(() => {});
  try {
    await server.listen(address);
    await assert.rejects(client.connect(address, "wrong"), /disconnected/);
    await assert.rejects(client.call("task", []), /unavailable/);
    await client.connect(address, "secret");
  } finally { client.close(); server.close(); }
});

test("Host shutdown rejects pending work and allows reconnection to a new Host", async () => {
  const address = endpoint();
  const server = new HostServer("old", new Map([["wait", () => new Promise(() => {})]]));
  const client = new HostClient(() => {});
  let replacement;
  try {
    await server.listen(address);
    await client.connect(address, "old");
    const pending = client.call("wait", []);
    const rejected = assert.rejects(pending, /disconnected/);
    server.close();
    await rejected;
    replacement = new HostServer("new", new Map([["status", () => "ready"]]));
    await replacement.listen(address);
    await client.connect(address, "new");
    assert.equal(await client.call("status", []), "ready");
  } finally { client.close(); replacement?.close(); server.close(); }
});
