import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/components/streamingText.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { advanceStreamingText, streamingGraphemes, streamingFrameInterval } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("long messages batch paints while retaining grapheme boundaries and exact content", () => {
  assert.equal(streamingFrameInterval(1000), 28);
  assert.equal(streamingFrameInterval(6000), 80);
  assert.equal(streamingFrameInterval(14000), 120);
  const target = "👨‍👩‍👧‍👦".repeat(1300);
  const first = advanceStreamingText("", target, 120);
  assert.ok(streamingGraphemes(first).length > 18);
  let visible = first;
  let frames = 1;
  while (visible !== target && frames++ < 1000) {
    const next = advanceStreamingText(visible, target, 120, true);
    assert.ok(next.length > visible.length);
    assert.equal(next.length % "👨‍👩‍👧‍👦".length, 0);
    visible = next;
  }
  assert.equal(visible, target);
});

test("a long Markdown table converges without dropping rows or delimiters", () => {
  const target = "| Name | Result |\n| --- | --- |\n" + "| 流式检查 | ✅ 成功 |\n".repeat(400);
  let visible = "";
  let frames = 0;
  while (visible !== target && frames++ < 3000) {
    visible = advanceStreamingText(visible, target, 80, true);
    assert.ok(target.startsWith(visible));
  }
  assert.equal(visible, target);
});

test("coarse chunks are bounded and converge exactly", () => {
  const target = "中".repeat(800);
  let visible = "";
  let frames = 0;
  while (visible !== target && frames++ < 500) {
    const next = advanceStreamingText(visible, target, 32);
    assert.ok(next.length > visible.length && next.length - visible.length <= 18);
    visible = next;
  }
  assert.equal(visible, target);
});

test("presentation budget responds to elapsed time, backlog and finalization", () => {
  const target = "中".repeat(900);
  const shortFrame = advanceStreamingText("", target, 16);
  const longFrame = advanceStreamingText("", target, 64);
  const finalizingFrame = advanceStreamingText("", target, 64, true);
  assert.ok(longFrame.length >= shortFrame.length);
  assert.ok(finalizingFrame.length >= longFrame.length);
  assert.ok(finalizingFrame.length <= 28);
});

test("sentence boundaries can end a paint before the hard budget", () => {
  const target = "这是第一句。这里是第二句，会继续生成。";
  const next = advanceStreamingText("", target, 96);
  assert.ok(next.endsWith("。") || next.length < target.length);
  assert.ok(target.startsWith(next));
});

test("emoji, combining marks and flags are never split", () => {
  const target = "👨‍👩‍👧‍👦e\u0301🇨🇳👍🏽中文";
  const boundaries = streamingGraphemes(target).map((_, i, parts) => parts.slice(0, i + 1).join(""));
  let visible = "";
  while (visible !== target) {
    visible = advanceStreamingText(visible, target, 32);
    assert.ok(boundaries.includes(visible));
  }
});

test("canonical replacements and empty content are authoritative", () => {
  assert.equal(advanceStreamingText("old", "replacement"), "replacement");
  assert.equal(advanceStreamingText("old", ""), "");
  assert.equal(advanceStreamingText("same", "same"), "same");
});
