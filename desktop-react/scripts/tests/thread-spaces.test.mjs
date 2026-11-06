// The 对话 / 建造 spaces: which one a conversation lives in, and where switching lands (src/state/threadSpaces.ts).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/state/threadSpaces.ts", import.meta.url), "utf8");
const module = { exports: {} };
new Function("module", "exports", ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText)(module, module.exports);
const { threadSpace, landingThread } = module.exports;

test("the Host's mark decides, a local choice overrides it", () => {
  assert.equal(threadSpace({ id: "a" }, [], []), "chat");
  assert.equal(threadSpace({ id: "a", weave: true }, [], []), "project");
  assert.equal(threadSpace({ id: "a", weave: true }, [], ["a"]), "chat");
  assert.equal(threadSpace({ id: "a" }, ["a"], []), "project");
});

const spaceOf = (thread) => thread.weave ? "project" : "chat";
const rows = [
  { id: "chat-old", updatedAt: "2026-11-01T00:00:00Z" },
  { id: "chat-new", updatedAt: "2026-11-05T00:00:00Z" },
  { id: "build-old", weave: true, updatedAt: "2026-11-02T00:00:00Z" },
  { id: "build-new", weave: true, updatedAt: "2026-11-04T00:00:00Z" },
  { id: "build-newest-gone", weave: true, archived: true, updatedAt: "2026-11-06T00:00:00Z" },
  { id: "build-fork", weave: true, parentThreadId: "build-old", updatedAt: "2026-11-05T12:00:00Z" },
];

test("lands on the conversation last open in that space", () => {
  assert.equal(landingThread(rows, spaceOf, "project", "build-old").id, "build-old");
  assert.equal(landingThread(rows, spaceOf, "chat", "chat-old").id, "chat-old");
  assert.equal(landingThread(rows, spaceOf, "project", "build-fork").id, "build-fork");
});

test("otherwise on the newest top-level one, never one of the other space or an archived one", () => {
  assert.equal(landingThread(rows, spaceOf, "project").id, "build-new");
  assert.equal(landingThread(rows, spaceOf, "chat").id, "chat-new");
  assert.equal(landingThread(rows, spaceOf, "project", "chat-new").id, "build-new");
  assert.equal(landingThread(rows, spaceOf, "project", "build-newest-gone").id, "build-new");
});

test("an empty space has nowhere to land", () => {
  assert.equal(landingThread(rows.filter((row) => !row.weave), spaceOf, "project"), undefined);
});
