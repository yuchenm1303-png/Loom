import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fromLoomRenderer, sentByLoomRenderer, trustRendererDocument } from "../../dist-electron/rendererIpc.js";

function fakeWindow(origin) {
  const mainFrame = { origin };
  let destroyed = false;
  return { webContents: { mainFrame }, isDestroyed: () => destroyed, destroy() { destroyed = true; } };
}

test("only the Loom document in the desktop window's main frame may call the renderer channels", () => {
  const window = fakeWindow("file://");
  const own = { sender: window.webContents, senderFrame: window.webContents.mainFrame };
  assert.equal(sentByLoomRenderer(own), false, "nothing is trusted before the renderer is loaded");

  trustRendererDocument(window, "file:///C:/Program%20Files/Loom/resources/app.asar/dist/index.html");
  assert.equal(sentByLoomRenderer(own), true);
  assert.equal(sentByLoomRenderer({ sender: {}, senderFrame: {} }), false, "another window");
  assert.equal(sentByLoomRenderer({ sender: window.webContents, senderFrame: { origin: "file://" } }), false, "an iframe");
  assert.equal(sentByLoomRenderer({ sender: window.webContents, senderFrame: null }), false, "a frame that is gone");

  window.webContents.mainFrame.origin = "https://example.com";
  assert.equal(sentByLoomRenderer(own), false, "the window navigated somewhere else");
  window.webContents.mainFrame.origin = "file://";
  window.destroy();
  assert.equal(sentByLoomRenderer(own), false, "a closed window");
});

test("the dev server origin is trusted only in development", () => {
  const window = fakeWindow("http://127.0.0.1:5173");
  trustRendererDocument(window, "http://127.0.0.1:5173");
  assert.equal(sentByLoomRenderer({ sender: window.webContents, senderFrame: window.webContents.mainFrame }), true);
  window.webContents.mainFrame.origin = "http://127.0.0.1:5174";
  assert.equal(sentByLoomRenderer({ sender: window.webContents, senderFrame: window.webContents.mainFrame }), false);
});

test("a wrapped handler refuses before running and passes arguments through", async () => {
  const window = fakeWindow("file://");
  trustRendererDocument(window, "file:///C:/Loom/dist/index.html");
  const calls = [];
  const handler = fromLoomRenderer((_event, first, second) => { calls.push([first, second]); return "done"; });
  assert.equal(handler({ sender: window.webContents, senderFrame: window.webContents.mainFrame }, "a", 2), "done");
  assert.throws(() => handler({ sender: {}, senderFrame: {} }, "b", 3), /did not come from the Loom window/);
  assert.deepEqual(calls, [["a", 2]]);
});

test("every renderer channel checks its sender", () => {
  const main = readFileSync(new URL("../main.ts", import.meta.url), "utf8");
  const registrations = [...main.matchAll(/ipcMain\.handle\("([^"]+)",\s*([^\n]*)/g)];
  assert.ok(registrations.length > 10, "renderer channels moved; update this test");
  // loom:disconnect returns a constant; the embedded browser checks with its own message.
  const unwrapped = registrations
    .filter(([, , rest]) => !rest.startsWith("fromLoomRenderer("))
    .map(([, channel]) => channel);
  assert.deepEqual(unwrapped, ["loom:disconnect", "loom:embedded-browser"]);
  assert.match(main, /"loom:embedded-browser"[\s\S]{0,200}!sentByLoomRenderer\(event\)/);

  const host = readFileSync(new URL("../hostProcess.ts", import.meta.url), "utf8");
  assert.match(host, /else ipcMain\.handle\(channel, fromLoomRenderer\(/, "Host channels forwarded from the window check their sender too");
});
