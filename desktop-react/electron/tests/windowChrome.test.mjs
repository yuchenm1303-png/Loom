import assert from "node:assert/strict";
import { mock, test } from "node:test";
let handler;
mock.module("electron", { namedExports: {
  ipcMain: { removeHandler() {}, handle(_channel, callback) { handler = callback; } },
} });
const { installWindowChrome } = await import("../../dist-electron/windowChrome.js");
test("window IPC validates sender, coordinates, maximize and controls", async () => {
  const calls = [];
  let maximized = false;
  const webContents = { mainFrame: {} };
  const window = { webContents, isDestroyed: () => false, isMaximized: () => maximized,
    getBounds: () => ({ x: 10, y: 20, width: 900, height: 600 }),
    maximize() { maximized = true; }, unmaximize() { maximized = false; },
    minimize() { calls.push("minimize"); }, close() { calls.push("close"); },
    setPosition(x, y) { calls.push([x, y]); }, on() {},
  };
  installWindowChrome(window);
  const event = { sender: webContents, senderFrame: webContents.mainFrame };
  assert.equal(await handler({ sender: {}, senderFrame: {} }, "close"), null);
  assert.equal(await handler({ ...event, senderFrame: {} }, "close"), null);
  assert.equal((await handler(event, "maximize")).maximized, true);
  await handler(event, "move", { x: 5, y: 10 });
  assert.deepEqual(calls, []);
  assert.equal((await handler(event, "maximize")).maximized, false);
  await handler(event, "move", { x: NaN, y: 20 });
  await handler(event, "move", { x: 45.3, y: 70.7 });
  await handler(event, "minimize"); await handler(event, "close");
  assert.deepEqual(calls, [[45, 71], "minimize", "close"]);
});

test("window controls keep addressing their own window after another is installed or closed", () => {
  const closed = [], callbacks = new Map();
  const make = id => ({ webContents: { mainFrame: {} }, isDestroyed: () => false, isMaximized: () => false,
    getBounds: () => ({ x: id, y: 0, width: 900, height: 600 }), close: () => closed.push(id),
    on: (name, callback) => callbacks.set(id + name, callback) });
  const first = make(1), second = make(2);
  installWindowChrome(first); installWindowChrome(second);
  const event = window => ({ sender: window.webContents, senderFrame: window.webContents.mainFrame });
  handler(event(first), "close");
  callbacks.get("1closed")();
  assert.equal(handler(event(first), "state"), null);
  assert.equal(handler(event(second), "state").x, 2);
  handler(event(second), "close");
  assert.deepEqual(closed, [1, 2]);
});
