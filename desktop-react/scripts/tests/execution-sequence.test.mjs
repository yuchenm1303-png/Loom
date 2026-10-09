import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/components/executionSequence.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { groupExecutionSequence, initialUpdateIds, isProcessCommentary } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const item = (id, type, extra = {}) => ({ id, type, turnId: "turn", status: "completed", ...extra });
const message = (id, extra = {}) => item(id, "assistant_message", { phase: "commentary", text: id, ...extra });
const ids = blocks => blocks.flatMap(b => b.kind === "activity" ? b.items : [b.item]).map(i => i.id);

test("initial update stays outside; commentary and tools form one ordered disclosure", () => {
  const input = [message("intro"), item("read", "tool_call"), message("finding"), item("edit", "file_edit"), message("latest", { status: "streaming" })];
  const result = groupExecutionSequence(input);
  assert.deepEqual(result.map(b => b.kind), ["item", "activity"]);
  assert.deepEqual(ids(result), input.map(i => i.id));
  assert.strictEqual(result[1].items[1], input[2], "retain the original message, including text and metadata");
});

test("finals, user intervention, decisions, errors, approvals and failed prose are boundaries", () => {
  for (const boundary of [message("final", { phase: "final_answer" }), message("legacy", { phase: undefined }),
    message("decision"), message("failed", { status: "failed" }), item("user", "user_message"), item("error", "error"), item("approval", "approval")]) {
    const result = groupExecutionSequence([item("before", "tool_call"), boundary, item("after", "tool_call")], new Set(["decision"]));
    assert.deepEqual(result.map(b => b.kind), ["activity", "item", "activity"]);
    assert.deepEqual(ids(result), ["before", boundary.id, "after"]);
  }
});

test("turn boundaries and unknown turn identity cannot join unrelated work", () => {
  const input = [item("one", "tool_call"), message("two", { turnId: "other" }), item("three", "tool_call", { turnId: undefined })];
  assert.deepEqual(groupExecutionSequence(input).map(b => b.kind), ["activity", "item", "activity"]);
});

test("append-only updates keep the execution group's first identity stable", () => {
  const base = [message("intro"), item("first", "tool_call")];
  const before = groupExecutionSequence(base);
  const after = groupExecutionSequence([...base, message("next"), item("second", "tool_call")]);
  assert.strictEqual(before[1].items[0], after[1].items[0]);
  assert.equal(after.length, 2);
});

test("only the first commentary after each user message, before any tool work, answers the user", () => {
  const input = [message("a"), item("t1", "tool_call"), message("b"), item("steer", "user_message"), message("c"), message("d"), item("t2", "tool_call"), message("e")];
  assert.deepEqual([...initialUpdateIds(input)], ["a", "c"]);
});

test("tool work, approvals and final answers end the chance to answer; position alone decides", () => {
  assert.deepEqual([...initialUpdateIds([item("t", "tool_call"), message("x")])], []);
  assert.deepEqual([...initialUpdateIds([item("ap", "approval"), message("x")])], []);
  assert.deepEqual([...initialUpdateIds([message("fin", { phase: "final_answer" }), message("x")])], []);
  // Identical wording, different place: only order matters.
  const same = [message("one", { text: "收到" }), item("t", "tool_call"), message("two", { text: "收到" })];
  assert.deepEqual([...initialUpdateIds(same)], ["one"]);
});

test("process commentary excludes decisions, failed prose, other phases and non-assistant items", () => {
  assert.equal(isProcessCommentary(message("ok")), true);
  assert.equal(isProcessCommentary(message("decision"), new Set(["decision"])), false);
  for (const status of ["failed", "denied", "interrupted"]) assert.equal(isProcessCommentary(message("x", { status })), false);
  assert.equal(isProcessCommentary(message("final", { phase: "final_answer" })), false);
  assert.equal(isProcessCommentary(message("legacy", { phase: undefined })), false);
  assert.equal(isProcessCommentary(item("tool", "tool_call")), false);
});
