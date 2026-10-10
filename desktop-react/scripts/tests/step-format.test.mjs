import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const compile = async name => {
  const source = readFileSync(new URL(`../../src/components/${name}.ts`, import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
};
const { formatStepDuration, lastOutputLine, firstFailureLine, parseTime } = await compile("stepFormat");
const { LIVE_ROW_WINDOW, foldedCount } = await compile("liveWindow");

test("a step that took under a second says nothing about time", () => {
  assert.equal(formatStepDuration(0), "");
  assert.equal(formatStepDuration(999), "");
  assert.equal(formatStepDuration(Number.NaN), "");
  assert.equal(formatStepDuration(-5), "");
});

test("durations read at the size that matters: tenths, seconds, minutes, hours", () => {
  assert.equal(formatStepDuration(1000), "1.0s");
  assert.equal(formatStepDuration(1280), "1.2s");
  assert.equal(formatStepDuration(9990), "9.9s");
  assert.equal(formatStepDuration(10_000), "10s");
  assert.equal(formatStepDuration(34_900), "34s");
  assert.equal(formatStepDuration(60_000), "1m 00s");
  assert.equal(formatStepDuration(125_000), "2m 05s");
  assert.equal(formatStepDuration(3_720_000), "1h 02m");
});

test("a running command is its last non-empty line, stripped of terminal control codes", () => {
  assert.equal(lastOutputLine("a\nb\n\n  \n"), "b");
  assert.equal(lastOutputLine("\u001b[32m✓ ok\u001b[0m\r\n"), "✓ ok");
  assert.equal(lastOutputLine("", "only stderr"), "only stderr", "the first stream with words wins");
  assert.equal(lastOutputLine("   ", "\n"), "");
  assert.equal(lastOutputLine(undefined, undefined), "");
  assert.equal(lastOutputLine("x".repeat(400)).length, 240, "one line, not a page");
  assert.equal(lastOutputLine("  progress   42%  \n"), "progress 42%", "runs of space collapse");
});

test("a failed step's reason is the first meaningful line of where it was kept", () => {
  assert.equal(firstFailureLine("", "\n\nAccess is denied.\nsecond"), "Access is denied.");
  assert.equal(firstFailureLine(undefined, "", "ENOENT"), "ENOENT");
  assert.equal(firstFailureLine("", ""), "");
});

test("a traceback's header says nothing: the reason is its last line", () => {
  const traceback = "Traceback (most recent call last):\n  File \"server.py\", line 3, in <module>\n    main()\nValueError: port 8772 is taken\n";
  assert.equal(firstFailureLine(traceback), "ValueError: port 8772 is taken");
  assert.equal(firstFailureLine("Traceback (most recent call last):\r\n"), "Traceback (most recent call last):", "nothing else to say: the header stays");
  assert.equal(firstFailureLine("Error: first\nTraceback (most recent call last):\nlast"), "Error: first", "only a leading header is skipped");
});

test("a progress bar that rewrites its line is what it ended as", () => {
  assert.equal(lastOutputLine("Downloading 10%\rDownloading 55%\rDownloading 100%"), "Downloading 100%");
  assert.equal(lastOutputLine("line one\r\nline two\r\n"), "line two", "ordinary Windows line endings are not rewrites");
  assert.equal(lastOutputLine("done\n\r\n"), "done");
});

test("timestamps that are not dates are not times", () => {
  assert.equal(parseTime(undefined), null);
  assert.equal(parseTime(""), null);
  assert.equal(parseTime("soon"), null);
  assert.equal(parseTime("2026-10-10T12:00:00Z"), Date.UTC(2026, 9, 10, 12, 0, 0));
});

test("a live stage keeps its newest steps and folds the older ones only when it saves a line", () => {
  assert.equal(LIVE_ROW_WINDOW, 6);
  assert.equal(foldedCount(0), 0);
  assert.equal(foldedCount(6), 0, "everything fits");
  assert.equal(foldedCount(7), 0, "one folded step would be replaced by a line: nothing gained");
  assert.equal(foldedCount(8), 2);
  assert.equal(foldedCount(76), 70);
  assert.equal(foldedCount(10, 4), 6, "the window is a parameter");
});
