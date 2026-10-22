import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { buildSync } from "esbuild";

const project = fileURLToPath(new URL("../..", import.meta.url));
const build = buildSync({ stdin: {
  resolveDir: project, contents: 'export { browserScreenshotPaths } from "./src/components/activityModel.ts";', loader: "ts",
}, bundle: true, platform: "node", format: "cjs", write: false, loader: { ".css": "empty" } });
const module = { exports: {} };
new Function("module", "exports", build.outputFiles[0].text)(module, module.exports);
const { browserScreenshotPaths: paths } = module.exports;
const item = (toolName, result) => ({ type: "tool_call", toolName, result });

test("workspace image observations preview their unique original files", () => {
  assert.deepEqual(paths(item("view_images", { images: [
    { path: "artifacts/frame.png" }, { path: "artifacts/detail.webp" }, { path: "artifacts/frame.png" },
    { path: "artifacts/page.html" }, null,
  ] })), ["artifacts/frame.png", "artifacts/detail.webp"]);
});

test("sequence observations preview the contact sheet and retain ordinary screenshot support", () => {
  assert.deepEqual(paths(item("browser_capture_sequence", { capture: { contact_sheet: "browser-screenshots/sequence/contact-sheet.png" } })),
    ["browser-screenshots/sequence/contact-sheet.png"]);
  assert.deepEqual(paths(item("browser_screenshot", { path: "browser-screenshots/current.png" })), ["browser-screenshots/current.png"]);
});

test("malformed or partial results do not create broken image cards", () => {
  assert.deepEqual(paths(item("view_images", { images: "not an array" })), []);
  assert.deepEqual(paths(item("browser_capture_sequence", { capture: { complete: false, frames: [] } })), []);
  assert.deepEqual(paths(item("echo", { path: "accidental.png" })), []);
});

test("browser acceptance failures still expose their captured evidence", () => {
  assert.deepEqual(paths(item("browser_run_check", {
    verification: { verdict: "failed" }, images: [{ path: "browser-screenshots/failed.png" }],
  })), ["browser-screenshots/failed.png"]);
});
