import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/components/executionSequence.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { groupExecutionSequence, initialUpdateIds, isProcessCommentary, settleLiveText, reportIds, bodyMessages, SUBSTANTIAL_TEXT_CHARS } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const item = (id, type, extra = {}) => ({ id, type, turnId: "turn", status: "completed", ...extra });
const message = (id, extra = {}) => item(id, "assistant_message", { phase: "commentary", text: id, ...extra });
// A sentence while it streams has no phase: the runtime classifies a step when its response completes.
const streaming = (id, text, extra = {}) => item(id, "assistant_message", { status: "streaming", text, ...extra });
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

const tool = (id, extra = {}) => item(id, "tool_call", { toolName: "exec", ...extra });
const failedTool = (id, extra = {}) => tool(id, { status: "failed", ...extra });
const user = (id, extra = {}) => item(id, "user_message", extra);
const reports = (items, released) => [...reportIds(items, released)];

test("the reply, a plan report and a long sentence are messages; other narration stays in the log", () => {
  const input = [user("u"), message("reply"), tool("t1", { stepId: "s1" }), message("chat", { stepId: "s2" }), tool("t2", { stepId: "s2" }),
    message("report", { stepId: "s3" }), tool("plan", { toolName: "update_plan", stepId: "s3" }),
    message("long", { text: "字".repeat(SUBSTANTIAL_TEXT_CHARS), stepId: "s4" }), tool("t4", { stepId: "s4" }),
    message("short", { text: "字".repeat(SUBSTANTIAL_TEXT_CHARS - 1), stepId: "s5" }), tool("t5", { stepId: "s5" })];
  assert.deepEqual(reports(input), ["reply", "report", "long"]);
});

test("a run that goes well says little; the sentence after a failed step is the model's reaction to it, and a message", () => {
  const input = [user("u"), message("reply"), failedTool("t1"), message("reaction"), tool("t2"), message("routine"), tool("t3"), message("routine2"),
    failedTool("t4"), message("again")];
  assert.deepEqual(reports(input), ["reply", "reaction", "again"]);
  const smooth = [user("u"), message("reply"), ...["a", "b", "c", "d", "e", "f"].flatMap(id => [message(`m-${id}`), tool(id)])];
  assert.deepEqual(reports(smooth), ["reply"], "no failures, no timer: only the reply");
});

test("a failure waits for the next sentence the model actually writes, and that sentence uses it up", () => {
  const input = [user("u"), message("reply"), failedTool("t1"), message("blank", { text: " " }), tool("t2"), message("reaction"), tool("t3"), message("routine")];
  assert.deepEqual(reports(input), ["reply", "reaction"]);
});

test("any failed call of a step counts, and a denied call too; sub-agent calls and a new user message do not", () => {
  const parallel = [user("u"), message("reply"), tool("a", { stepId: "s1" }), failedTool("b", { stepId: "s1" }), tool("c", { stepId: "s1" }), message("m1")];
  assert.deepEqual(reports(parallel), ["reply", "m1"]);
  assert.deepEqual(reports([user("u"), message("reply"), tool("a", { status: "denied" }), message("m1")]), ["reply", "m1"]);
  assert.deepEqual(reports([user("u"), message("reply"), failedTool("n", { nested: true }), message("m1")]), ["reply"], "a sub-agent's trouble is not this run's");
  assert.deepEqual(reports([user("u"), message("reply"), failedTool("a"), user("steer"), tool("b"), message("m1")]), ["reply"], "a new message from the user starts afresh");
});

test("blank sentences are never messages, and a sentence already drawn stays one", () => {
  const input = [user("u"), message("reply"), tool("t1"), message("blank", { text: "  " }), tool("t2"), message("quiet")];
  assert.deepEqual(reports(input), ["reply"]);
  assert.deepEqual(reports(input, new Set(["quiet"])), ["reply", "quiet"]);
});

test("a sentence whose tool call is already streaming is narration, whatever it says", () => {
  const input = [user("u"), message("intro"), tool("t1"), streaming("s", "好"), tool("t2", { status: "streaming_arguments" })];
  const { items, held } = settleLiveText(input);
  assert.equal(held, "");
  assert.equal(items[3].phase, "commentary");
  assert.equal(items[3].status, "streaming", "it is still live");
  assert.equal(input[3].phase, undefined, "the source item is untouched");
  assert.strictEqual(settleLiveText(input).items[3], items[3], "one variant per item, so it does not re-render for nothing");
  const blocks = groupExecutionSequence(items);
  assert.deepEqual(blocks.map(b => b.kind), ["item", "item", "activity"], "the user and the first reply, then one work log");
  assert.deepEqual(blocks[2].items.map(i => i.id), ["t1", "s", "t2"], "it joins the work log instead of splitting it");
});

test("a short sentence streaming after tool work waits until it is long enough or its hold runs out", () => {
  const worked = [user("u"), message("intro"), tool("t1", { stepId: "s1" })];
  const input = [...worked, streaming("s", "编码问题。")];
  const hold = settleLiveText(input);
  assert.deepEqual(hold.items.map(i => i.id), ["u", "intro", "t1"]);
  assert.equal(hold.held, "s");
  const released = settleLiveText(input, new Set(["s"]));
  assert.deepEqual(released.items.map(i => i.id), ["u", "intro", "t1", "s"]);
  assert.equal(released.items.at(-1).phase, "commentary", "a short one that waited is narration in the work log");
  assert.equal(released.held, "");
  assert.deepEqual(settleLiveText([...worked, streaming("s", "字".repeat(SUBSTANTIAL_TEXT_CHARS))]).items.map(i => i.id), ["u", "intro", "t1", "s"]);
  assert.equal(settleLiveText([...worked, streaming("s", "字".repeat(SUBSTANTIAL_TEXT_CHARS - 1))]).held, "s");
});

test("a sentence that will be a message is drawn at once, so it is never drawn and then taken back", () => {
  const afterSuccess = [user("u"), message("intro"), tool("t1")];
  const afterFailure = [user("u"), message("intro"), failedTool("t1")];
  assert.equal(settleLiveText([...afterSuccess, streaming("s", "好")]).held, "s", "routine after a success: held");
  const reaction = settleLiveText([...afterFailure, streaming("s", "好")]);
  assert.equal(reaction.held, "", "the reaction to a failure is a message from its first word");
  assert.equal(reaction.items.at(-1).phase, "commentary", "a short one is narration, so it sits in the work log from the start");
  assert.equal(reaction.items.at(-1).status, "streaming");
  const long = settleLiveText([...afterSuccess, streaming("s", "字".repeat(SUBSTANTIAL_TEXT_CHARS))]);
  assert.equal(long.items.at(-1).phase, undefined, "a long one may be the answer itself, so it stays an ordinary message");
  // Once it completes as narration it is still a message, because the verdict only looks backwards.
  const completed = [...afterFailure, message("s", { text: "好", stepId: "next" }), tool("next-tool", { stepId: "next" })];
  assert.ok(reportIds(completed).has("s"));
});

test("the first sentence after a user message answers it, so it is never held", () => {
  const first = [user("u"), streaming("s", "好")];
  assert.equal(settleLiveText(first).held, "");
  assert.strictEqual(settleLiveText(first).items, first);
  const steered = [user("u"), message("a"), tool("t"), user("steer"), streaming("s", "好")];
  assert.equal(settleLiveText(steered).held, "");
  // Identical wording, different place: only position, status and length decide.
  assert.equal(settleLiveText([user("u"), tool("t"), streaming("s", "好")]).held, "s");
});

test("reasoning that is on screen is never held, and finished items are untouched", () => {
  const worked = [user("u"), tool("t")];
  for (const extra of [{ reasoning: "先想想" }, { text: "<think>先想想" }]) assert.equal(settleLiveText([...worked, streaming("s", "好", extra)]).held, "");
  const done = [...worked, message("c"), message("f", { phase: "final_answer" }), item("legacy", "assistant_message", { text: "x" }), message("late", { status: "streaming" })];
  assert.strictEqual(settleLiveText(done).items, done);
});

test("what stays on screen when the work log folds: the model's messages in order, never hidden narration", () => {
  const input = [user("u"), message("reply"), failedTool("t1"), message("reaction"), tool("t2"), message("routine"), tool("t3"),
    message("long", { text: "字".repeat(SUBSTANTIAL_TEXT_CHARS) }), message("blank", { text: " " })];
  assert.deepEqual(bodyMessages(input, reportIds(input), new Set()).map(i => i.id), ["reply", "reaction", "long"]);
});

test("ordinary assistant messages stay too: decisions, failed prose and histories written before phases", () => {
  const input = [user("u"), message("reply"), tool("t1"), message("decision", { text: "```loom-decision\n{}\n```" }),
    message("failed", { status: "failed", text: "中断" }), item("legacy", "assistant_message", { text: "旧记录" }),
    item("empty", "assistant_message", { text: "  " }), message("routine")];
  const kept = bodyMessages(input, reportIds(input), new Set(["decision"])).map(i => i.id);
  assert.deepEqual(kept, ["reply", "decision", "failed", "legacy"], "and an empty one is nothing to keep");
});
