import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";
const source = readFileSync(new URL("../../src/components/browserActivityModel.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { collectBrowserActivities: collect, matchingBrowserTab: match, browserPageUrl, browserImagePreview, browserActivityOpenCommand: openCommand } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
function tool(name, args, result, status = "completed") { return { id: "event", type: "tool_call", toolName: name, arguments: args, result, status }; }
const opened = tool("browser_open", {}, { browser_id: "b1", browser_connection: "embedded", url: "https://example.com/", title: "Example", page_info: { tab_id: "t1" } });
test("one card tracks the latest observation and screenshot without duplicating actions", () => {
  const pages = collect([opened, tool("browser_screenshot", { browser_id: "b1" }, { path: "browser-screenshots/one.png" }), tool("browser_click", { browser_id: "b1" }, { url: "https://example.com/", title: "Updated" })], true);
  assert.equal(pages.length, 1);
  assert.equal(pages[0].title, "Updated");
  assert.equal(pages[0].screenshot, "browser-screenshots/one.png");
  assert.equal(pages[0].connection, "embedded");
  assert.equal(pages[0].tabId, "t1");
});
test("navigation discards the previous page screenshot and title", () => {
  const pages = collect([opened, tool("browser_screenshot", { browser_id: "b1" }, { path: "one.png" }), tool("browser_navigate", { browser_id: "b1" }, { url: "https://example.com/next" })], true);
  assert.equal(pages[0].screenshot, "");
  assert.equal(pages[0].title, "");
});
test("status follows the latest call and active turns only", () => {
  const pending = tool("browser_click", { browser_id: "b1" }, {}, "running");
  assert.equal(collect([opened, pending], true)[0].status, "working");
  assert.equal(collect([opened, pending], false)[0].status, "ready");
  assert.equal(collect([opened, { ...pending, status: "waiting_approval" }], true)[0].status, "waiting");
  assert.equal(collect([opened, { ...pending, status: "failed" }], true)[0].status, "failed");
  assert.equal(collect([opened, tool("browser_close", { browser_id: "b1" }, {})], false)[0].status, "closed");
});
test("multiple browsers stay separate; discovery and failed opens do not become cards", () => {
  assert.equal(collect([tool("browser_backends", {}, {}), tool("browser_open", { url: "https://example.com/" }, {}, "failed")], true).length, 0);
  assert.equal(collect([opened, tool("browser_open", {}, { browser_id: "b2", url: "https://example.org/" })], true).length, 2);
});
test("only embedded identity can select a live tab; a closed identity cannot match by URL", () => {
  const target = collect([opened], false)[0];
  const tabs = [{ id: "t1", url: "https://example.com/new" }, { id: "t2", url: target.url }];
  assert.equal(match(target, tabs), "t1");
  assert.equal(match({ ...target, tabId: "missing" }, tabs), null);
  assert.equal(match({ ...target, connection: "isolated" }, tabs), null);
  assert.equal(match({ ...target, tabId: "" }, tabs), "t2");
});
test("only navigable http pages without credentials are offered", () => {
  for (const url of ["javascript:alert(1)", "file:///C:/test", "https://user:password@example.com/", "https://example.com/?token=[REDACTED]", "invalid"]) assert.equal(browserPageUrl(url), "");
  assert.equal(browserPageUrl("http://127.0.0.1:8765/page"), "http://127.0.0.1:8765/page");
});

test("direct image pages provide previews even without browser_screenshot", () => {
  const url = "http://127.0.0.1:8769/artifacts/loom-home-appcheck.png";
  assert.deepEqual(browserImagePreview(url), { url, localPath: "artifacts/loom-home-appcheck.png" });
  assert.equal(browserImagePreview("https://example.com/photo.webp?v=2").localPath, "");
  assert.equal(browserImagePreview("http://localhost:8769/other/photo.png").localPath, "");
  assert.equal(browserImagePreview("http://localhost:8769/artifacts/%2e%2e%2fsecret.png").localPath, "");
  assert.equal(browserImagePreview("https://example.com/page.html"), null);
});

test("clicking an external card opens its page immediately without selecting colliding external IDs", () => {
  const target = { ...collect([opened], false)[0], connection: "isolated" };
  assert.deepEqual(openCommand(target, [{ id: "t1", url: "https://unrelated.example/" }]), { action: "new-tab", url: target.url });
  assert.deepEqual(openCommand(target, [{ id: "copy", url: target.url }]), { action: "select-tab", tabId: "copy" });
});

test("embedded cards retain live identity; unavailable tabs reopen directly", () => {
  const target = collect([opened], false)[0];
  assert.deepEqual(openCommand(target, [{ id: "t1", url: "https://example.com/new" }]), { action: "select-tab", tabId: "t1" });
  assert.deepEqual(openCommand(target, []), { action: "new-tab", url: target.url });
  assert.deepEqual(openCommand({ ...target, status: "closed" }, [{ id: "t1", url: "https://unrelated.example/" }]), { action: "new-tab", url: target.url });
});
