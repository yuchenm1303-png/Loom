import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/components/profileInsightsModel.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const model = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

function rangeDays(start, count) {
  const days = [];
  const cursor = model.parseLocalDate(start);
  for (let index = 0; index < count; index += 1) {
    days.push({ date: model.localDateKey(cursor), totalTokens: 0 });
    cursor.setDate(cursor.getDate() + 1);
  }
  return days;
}

test("weeks are Monday-first and padded at both edges", () => {
  // 2025-09-21 is a Sunday; 2026-09-26 is a Saturday.
  const days = rangeDays("2025-09-21", 371);
  const weeks = model.buildWeeks(days, "2025-09-21", "2026-09-26");
  assert.equal(weeks[0][0].date, "2025-09-15");
  assert.deepEqual(weeks[0].map((cell) => Boolean(cell.day)), [false, false, false, false, false, false, true]);
  const last = weeks.at(-1);
  assert.equal(last[0].date, "2026-09-21");
  assert.deepEqual(last.map((cell) => Boolean(cell.day)), [true, true, true, true, true, true, false]);
  assert.equal(weeks.flat().filter((cell) => cell.day).length, 371);
});

test("each month is marked once, on its first whole-week column", () => {
  const days = rangeDays("2025-09-21", 371);
  const weeks = model.buildWeeks(days, "2025-09-21", "2026-09-26");
  const markers = model.monthMarkers(weeks);
  const keys = markers.map((marker) => `${marker.date.getFullYear()}-${marker.date.getMonth() + 1}`);
  assert.equal(new Set(keys).size, keys.length, "no month may be labelled twice");
  assert.deepEqual(keys, [
    "2025-9", "2025-10", "2025-11", "2025-12", "2026-1", "2026-2", "2026-3",
    "2026-4", "2026-5", "2026-6", "2026-7", "2026-8", "2026-9",
  ]);
  // October 2025 starts on a Wednesday, so its marker sits on the week of the 6th.
  assert.equal(weeks[markers[1].column][0].date, "2025-10-06");
  assert.deepEqual(markers.filter((marker) => marker.isYearStart).map((marker) => marker.date.getFullYear()), [2026]);
});

test("a partial first month too close to the next marker loses its label", () => {
  // 2025-09-28 is a Sunday: September gets one column before October's marker.
  const days = rangeDays("2025-09-28", 60);
  const weeks = model.buildWeeks(days, "2025-09-28", "2025-11-26");
  const markers = model.monthMarkers(weeks);
  assert.equal(markers[0].date.getMonth(), 9, "October must be the first label");
});

test("heat levels follow percentile rank among active days", () => {
  const values = [0, 10, 20, 30, 40, 50, 60, 70, 80_000_000];
  const level = model.heatScale(values);
  assert.equal(level(0), 0);
  assert.equal(level(10), 1);
  assert.equal(level(80_000_000), 4);
  assert.deepEqual([20, 30, 40, 50, 60, 70].map(level), [1, 2, 2, 3, 3, 4]);
  // Ties share a level; a lone active day is the busiest one.
  assert.equal(model.heatScale([5, 5, 5, 5])(5), 4);
  assert.equal(model.heatScale([0, 0, 42])(42), 4);
  assert.equal(model.heatScale([])(9), 0);
});

test("compact numbers keep a decimal only while it still means something", () => {
  assert.equal(model.formatCompact(169_920_185, "zh-CN"), "1.7亿");
  assert.equal(model.formatCompact(54_970_823, "zh-CN"), "5497万");
  assert.equal(model.formatCompact(3_667_656, "zh-CN"), "366.8万");
  assert.equal(model.formatCompact(950, "zh-CN"), "950");
  assert.equal(model.formatCompact(54_970_823, "en-US"), "55M");
  assert.equal(model.formatCompact(1_500, "en-US"), "1.5K");
  assert.equal(model.formatCompact(-4, "en-US"), "0");
});

test("durations, hours and relative days read naturally", () => {
  assert.equal(model.formatDuration(5363.5, true), "1 小时 29 分");
  assert.equal(model.formatDuration(45, true), "45 秒");
  assert.equal(model.formatDuration(125, false), "2m 5s");
  assert.equal(model.hourLabel(6, true), "早上 6 点");
  assert.equal(model.hourLabel(12, true), "中午 12 点");
  assert.equal(model.hourLabel(21, true), "晚上 9 点");
  assert.equal(model.hourLabel(0, false), "12 AM");
  assert.equal(model.relativeDayLabel("2026-09-26", "2026-09-26", true), "今天");
  assert.equal(model.relativeDayLabel("2026-09-25", "2026-09-26", true), "昨天");
  assert.equal(model.relativeDayLabel("2026-09-23", "2026-09-26", false), "3 days ago");
  assert.equal(model.relativeDayLabel("2026-09-10", "2026-09-26", true), null);
  // Crossing the end of daylight saving still counts whole days.
  assert.equal(model.daysBetween("2026-10-31", "2026-11-02"), 2);
});

test("known tools get display names and unknown ones keep their id", () => {
  assert.equal(model.toolLabel("exec", true), "运行命令");
  assert.equal(model.toolLabel("read_workspace_text", false), "Read file");
  assert.equal(model.toolLabel("some_mcp_tool", true), null);
});
