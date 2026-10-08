import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/components/streamingText.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { advanceStreamingText, healStreamingMarkdown, streamingGraphemes, streamingFrameInterval } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

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

test("final provider bursts progress across the handoff window instead of dumping at its end", () => {
  const target = "完成。段落，内容。\n".repeat(1000);
  let visible = "";
  const checkpoints = [];
  for (let elapsed = 0; elapsed < 420; elapsed += 28) {
    visible = advanceStreamingText(visible, target, 28, true, 420 - elapsed);
    assert.ok(target.startsWith(visible));
    checkpoints.push(visible.length);
  }
  assert.ok(checkpoints[3] > target.length * .15, "meaningful early progress despite punctuation");
  assert.ok(checkpoints[9] > target.length * .5, "most text lands before the final frame");
  assert.equal(visible, target);
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

test("an open inline code span grows as a chip instead of showing its backtick", () => {
  assert.equal(healStreamingMarkdown("先看 `validat"), "先看 `validat`");
  assert.equal(healStreamingMarkdown("先看 `"), "先看 ");
  assert.equal(healStreamingMarkdown("`a` 和 `b"), "`a` 和 `b`");
  assert.equal(healStreamingMarkdown("``code` inside"), "``code` inside``");
  assert.equal(healStreamingMarkdown("完整的 `code` 段落"), "完整的 `code` 段落");
});

test("bold and strike close at the growth edge without swallowing spaces", () => {
  assert.equal(healStreamingMarkdown("请输入**有效的"), "请输入**有效的**");
  assert.equal(healStreamingMarkdown("请输入**有效的 "), "请输入**有效的** ");
  assert.equal(healStreamingMarkdown("结尾 **"), "结尾 ");
  assert.equal(healStreamingMarkdown("~~旧方案"), "~~旧方案~~");
  assert.equal(healStreamingMarkdown("`a ** b` 继续"), "`a ** b` 继续");
});

test("fences, bare markers and tables wait until their meaning is settled", () => {
  assert.equal(healStreamingMarkdown("代码如下：\n\n```t"), "代码如下：\n");
  assert.equal(healStreamingMarkdown("代码如下：\n\n```ts\nconst a = 1;"), "代码如下：\n\n```ts\nconst a = 1;");
  assert.equal(healStreamingMarkdown("```ts\nconst a = 1;\n``"), "```ts\nconst a = 1;");
  assert.equal(healStreamingMarkdown("```ts\nconst a = `x\n```\n继续 `y"), "```ts\nconst a = `x\n```\n继续 `y`");
  assert.equal(healStreamingMarkdown("段落\n\n##"), "段落");
  assert.equal(healStreamingMarkdown("列表：\n- 第一项\n- "), "列表：\n- 第一项");
  assert.equal(healStreamingMarkdown("结果：\n\n| 名称 | 结果"), "结果：");
  assert.equal(healStreamingMarkdown("| 名称 | 结果 |\n| --- | -"), "| 名称 | 结果 |\n| --- | -");
});

test("links show their label while the target arrives; indexes and stickers are left alone", () => {
  assert.equal(healStreamingMarkdown("参见 [登录文档](docs/lo"), "参见 登录文档");
  assert.equal(healStreamingMarkdown("参见 [登录文"), "参见 登录文");
  assert.equal(healStreamingMarkdown("截图 ![页面](shot"), "截图 ");
  assert.equal(healStreamingMarkdown("取 items[0"), "取 items[0");
  assert.equal(healStreamingMarkdown("好的[[AI_LEDGER_INLINE_STICKER:joy"), "好的[[AI_LEDGER_INLINE_STICKER:joy");
  assert.equal(healStreamingMarkdown("完成 [文档](docs/a.md) 了"), "完成 [文档](docs/a.md) 了");
  assert.equal(healStreamingMarkdown("`arr[0"), "`arr[0`");
});
