// What the hub and the phone say about a sub-agent's run and about the record of all of them (src/components/agentPeek.ts).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/components/agentPeek.ts", import.meta.url), "utf8");
const module = { exports: {} };
new Function("module", "exports", ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText)(module, module.exports);
const { agentRoleName, durationText, historyEntry, outcomeText, resultHeadline, tokensText, whenText } = module.exports;

const now = Date.parse("2026-11-10T12:00:00");

test("durations read as a person would say them", () => {
  const at = (minutes) => new Date(now - minutes * 60000).toISOString();
  assert.equal(durationText(at(0), null, now), "不到 1 分");
  assert.equal(durationText(at(3), null, now), "3 分");
  assert.equal(durationText(at(80), at(20), now), "1 时");
  assert.equal(durationText(at(200), null, now), "3 时 20 分");
  assert.equal(durationText(at(80), null, now, "en"), "1 h 20 min");
  assert.equal(durationText(undefined, null, now), "", "an unknown start says nothing");
});

test("a record says when it happened: the time today, yesterday with its time, else the date", () => {
  const day = (offset, time) => { const date = new Date(now); date.setDate(date.getDate() + offset); const [h, m] = time.split(":"); date.setHours(Number(h), Number(m), 0, 0); return date.toISOString(); };
  assert.equal(whenText(day(0, "09:05"), now), "09:05");
  assert.equal(whenText(day(-1, "14:02"), now), "昨天 14:02");
  assert.equal(whenText(day(-1, "14:02"), now, "en"), "Yesterday 14:02");
  assert.match(whenText(day(-3, "08:30"), now), /^\d+\/\d+ 08:30$/);
  assert.equal(whenText(null, now), "");
});

test("cost and outcome are short words", () => {
  assert.equal(tokensText(0), "");
  assert.equal(tokensText(120_000), "120K");
  assert.equal(tokensText(3_613_960), "3.6M");
  assert.equal(tokensText(400), "1K");
  assert.deepEqual(["", "done", "failed", "stopped"].map((value) => outcomeText(value)), ["进行中", "已完成", "没做完", "已停止"]);
  assert.equal(agentRoleName("goal-line"), "并行支路");
  assert.equal(agentRoleName("goal-check", "en"), "Independent check");
  assert.equal(agentRoleName("something-new"), "子代理");
});

test("the headline of a result skips the heading above it", () => {
  assert.equal(resultHeadline("## 报告\n跑完 4 个信道条件，吞吐增益 +6.1%\n文件 results/a.json"), "跑完 4 个信道条件，吞吐增益 +6.1%");
  assert.equal(resultHeadline("## 只有标题"), "只有标题");
  assert.equal(resultHeadline(""), "");
  assert.ok(resultHeadline("x".repeat(300), 20).length <= 20);
});

test("a history entry: what it was for, how it ended, how long, what it cost, when", () => {
  const row = { id: "a", parentId: "t", role: "goal-line", task: "正式实验：P3 链路自适应", state: "completed", outcome: "done", finished: true,
    startedAt: new Date(now - 90 * 60000).toISOString(), endedAt: new Date(now - 10 * 60000).toISOString(), tokens: 3_613_960,
    result: "## 报告\n跑完 4 个信道条件", error: "" };
  const entry = historyEntry(row, now);
  assert.equal(entry.title, "正式实验：P3 链路自适应");
  assert.equal(entry.headline, "跑完 4 个信道条件");
  assert.equal(entry.meta, "并行支路 · 1 时 20 分 · 3.6M · 11:50");
  assert.equal(entry.outcome, "done");
  const failed = historyEntry({ ...row, task: "", outcome: "failed", result: "", error: "模型服务额度已耗尽" }, now);
  assert.equal(failed.title, "并行支路", "no task: the role names it");
  assert.equal(failed.headline, "模型服务额度已耗尽", "a failure says why before anything it handed back");
});
