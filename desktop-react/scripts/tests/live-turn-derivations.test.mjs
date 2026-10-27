// A long live turn re-derives its step descriptions on every streamed update. They are cached per item
// object (items are replaced, never mutated, when they change); these tests hold the cache to that contract:
// a changed item is described afresh, an unchanged one is not described again.
import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const bundle = await build({
  entryPoints: [fileURLToPath(new URL("../../src/components/activityModel.ts", import.meta.url))],
  bundle: true,
  write: false,
  format: "esm",
  platform: "node",
  jsx: "automatic",
  loader: { ".css": "empty", ".svg": "dataurl", ".png": "dataurl" },
  logLevel: "error",
});
const { buildActivityRows, describeRow, groupSummary, turnProcessBreakdown } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`);
const { commandFromItem, normalizedToolName, runtimeCopy } = await import(
  `data:text/javascript;base64,${Buffer.from((await build({
    entryPoints: [fileURLToPath(new URL("../../src/components/runtimeCopy.ts", import.meta.url))],
    bundle: true, write: false, format: "esm", platform: "node", jsx: "automatic", logLevel: "error",
    loader: { ".css": "empty", ".svg": "dataurl", ".png": "dataurl" },
  })).outputFiles[0].text).toString("base64")}`);

const copy = runtimeCopy("en");
const call = (id, extra = {}) => ({ id, type: "tool_call", turnId: "t", status: "completed", toolName: "read_workspace_text", arguments: { path: "a.txt" }, ...extra });

test("a step is described once while it stays the same object", () => {
  const item = call("a");
  const [row] = buildActivityRows([item]);
  const first = describeRow(row, copy);
  const [again] = buildActivityRows([item]);
  assert.equal(describeRow(again, copy), first, "a rebuilt row over the same item reuses the description");
});

test("a step that changes is described afresh", () => {
  const running = call("b", { status: "running", arguments: { path: "old.txt" } });
  const before = describeRow(buildActivityRows([running])[0], copy);
  const done = { ...running, status: "failed", arguments: { path: "new.txt" } };
  const after = describeRow(buildActivityRows([done])[0], copy);
  assert.notEqual(after, before);
  assert.match(after.target, /new\.txt/);
  assert.notEqual(after.verb, before.verb, "the tense follows the new status");
});

test("the wrapper, the language and the edit delta are part of what a description depends on", () => {
  const exec = call("c", { toolName: "exec", arguments: { command: "npm test" } });
  const process = { id: "p", type: "process", turnId: "t", status: "running", argv: ["npm", "test"] };
  const [row] = buildActivityRows([exec, process]);
  assert.equal(row.wrapper, exec);
  const english = describeRow(row, copy);
  const chinese = describeRow(row, runtimeCopy("zh"));
  assert.notEqual(chinese.verb, english.verb);
  const failedWrapper = { ...exec, status: "failed" };
  assert.equal(describeRow(row, copy).verb, english.verb);
  assert.notEqual(describeRow({ ...row, wrapper: failedWrapper }, copy).verb, english.verb, "a new wrapper is a new description");

  const edit = { id: "e", type: "file_edit", turnId: "t", status: "completed", paths: ["x.ts", "y.ts"], diff: "" };
  const [editRow] = buildActivityRows([edit]);
  const whole = describeRow(editRow, copy);
  const delta = { paths: ["y.ts"], diff: "", added: 0, removed: 0 };
  assert.notEqual(describeRow(editRow, copy, delta).target, whole.target);
  assert.equal(describeRow(editRow, copy).target, whole.target);
});

test("summaries over a long run use the cached descriptions and still count every step", () => {
  const items = Array.from({ length: 300 }, (_, index) => call(`s${index}`, { toolName: index % 3 ? "browser_click" : "read_workspace_text" }));
  const rows = buildActivityRows(items);
  const summary = groupSummary(rows, copy);
  assert.equal(groupSummary(buildActivityRows(items), copy), summary);
  const breakdown = turnProcessBreakdown(items, copy);
  assert.equal(breakdown.filesRead + breakdown.tools + breakdown.searches, 300);
});

test("tool names and commands are read once per item and follow a replaced item", () => {
  const item = call("n", { toolName: " Exec-Command ", arguments: { command: "git status" } });
  assert.equal(normalizedToolName(item), "exec_command");
  assert.equal(commandFromItem(item), "git status");
  const replaced = { ...item, toolName: "shell", arguments: { command: "git log" } };
  assert.equal(normalizedToolName(replaced), "shell");
  assert.equal(commandFromItem(replaced), "git log");
  assert.equal(commandFromItem(item), "git status");
});
