import assert from "node:assert/strict";
import { test } from "node:test";
import { installRendererContentSecurity, rendererContentSecurityPolicy } from "../../dist-electron/contentSecurity.js";

const parse = (policy) => Object.fromEntries(policy.split(";").map((part) => part.trim().split(/\s+/)).map(([name, ...values]) => [name, values]));
const built = { url: "file:///C:/Users/me/AppData/Local/Programs/Loom/resources/app.asar/dist/index.html", development: false };
const dev = { url: "http://127.0.0.1:5173", development: true };

test("the policy names every kind of content the window may load and nothing else", () => {
  const policy = parse(rendererContentSecurityPolicy(built));
  assert.deepEqual(policy["default-src"], ["'none'"]);
  // Only Loom's own files run: no inline script, no eval, no remote script, no blob: script.
  assert.deepEqual(policy["script-src"], ["'self'"]);
  assert.deepEqual(policy["object-src"], ["'none'"]);
  assert.deepEqual(policy["base-uri"], ["'none'"]);
  assert.deepEqual(policy["form-action"], ["'none'"]);
  // Artifact previews are the only frames; their pages are served by the loom-artifact: protocol.
  assert.deepEqual(policy["frame-src"], ["loom-artifact:"]);
  assert.ok(policy["img-src"].includes("loom-artifact:") && policy["media-src"].includes("loom-artifact:"));
  assert.ok(!policy["connect-src"].some((source) => /^(https?|wss?):/.test(source)), "the packaged window never talks to the network itself");
  assert.ok(!policy["script-src"].includes("'unsafe-inline'") && !policy["script-src"].includes("'unsafe-eval'"));
});

test("only the development window may reach the dev server's reload channel", () => {
  assert.deepEqual(parse(rendererContentSecurityPolicy(dev))["connect-src"],
    ["'self'", "blob:", "data:", "http://127.0.0.1:5173", "ws://127.0.0.1:5173"]);
  assert.ok(!rendererContentSecurityPolicy(built).includes("ws:"));
  assert.deepEqual(parse(rendererContentSecurityPolicy(dev))["script-src"], ["'self'"]);
});

function windowWith(id) {
  let registered = null;
  let destroyed = false;
  const webContents = { id, session: { webRequest: { onHeadersReceived: (filter, listener) => { registered = { filter, listener }; } } } };
  return { window: { webContents, isDestroyed: () => destroyed }, registered: () => registered, destroy: () => { destroyed = true; } };
}

function respond(registered, details) {
  let answer;
  registered.listener({ responseHeaders: { "Content-Type": ["text/html"] }, ...details }, (value) => { answer = value; });
  return answer;
}

test("the header goes on the window's own document and on nothing else that shares its session", () => {
  const { window, registered } = windowWith(7);
  installRendererContentSecurity(window, built);
  assert.deepEqual(registered().filter, { urls: ["file:///*"] });
  const own = respond(registered(), { resourceType: "mainFrame", webContentsId: 7, url: built.url + "#/settings" });
  assert.equal(own.responseHeaders["Content-Security-Policy"][0], rendererContentSecurityPolicy(built));
  assert.deepEqual(own.responseHeaders["Content-Type"], ["text/html"], "other headers are kept");

  // An artifact preview window uses the same session and shows workspace files the policy would break.
  assert.deepEqual(respond(registered(), { resourceType: "mainFrame", webContentsId: 8, url: "file:///C:/work/report.html" }), {});
  assert.deepEqual(respond(registered(), { resourceType: "subFrame", webContentsId: 7, url: built.url }), {});
  assert.deepEqual(respond(registered(), { resourceType: "script", webContentsId: 7, url: "file:///C:/x/app.asar/dist/assets/index.js" }), {});
  assert.deepEqual(respond(registered(), { resourceType: "mainFrame", webContentsId: 7, url: "not a url" }), {});
});

test("the development server's document gets the policy and another origin does not", () => {
  const { window, registered } = windowWith(3);
  installRendererContentSecurity(window, dev);
  assert.deepEqual(registered().filter, { urls: ["http://127.0.0.1:5173/*"] });
  assert.ok(respond(registered(), { resourceType: "mainFrame", webContentsId: 3, url: "http://127.0.0.1:5173/" }).responseHeaders);
  assert.deepEqual(respond(registered(), { resourceType: "mainFrame", webContentsId: 3, url: "http://127.0.0.1:9999/" }), {});
});

test("a destroyed window gets no header", () => {
  const { window, registered, destroy } = windowWith(5);
  installRendererContentSecurity(window, built);
  destroy();
  assert.deepEqual(respond(registered(), { resourceType: "mainFrame", webContentsId: 5, url: built.url }), {});
});
