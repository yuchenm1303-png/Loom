import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const load = async (relative) => {
  const source = readFileSync(new URL(relative, import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
};
const { HOME_USAGE_STORAGE_KEY, HOME_USAGE_MAX_AGE_DAYS, trimHomeUsage, readStoredHomeUsage, storeHomeUsage } =
  await load("../../src/components/homeUsageCache.ts");

const NOW = new Date(2026, 9, 11, 9, 30); // local 2026-10-11

function day(offset) {
  const stamp = new Date(NOW.getFullYear(), NOW.getMonth(), NOW.getDate() - offset);
  return `${stamp.getFullYear()}-${String(stamp.getMonth() + 1).padStart(2, "0")}-${String(stamp.getDate()).padStart(2, "0")}`;
}

/** What profile/insights answers: far more than the card reads. */
function fullAnswer(endOffset = 0) {
  return {
    generatedAt: "2026-10-11T04:00:00Z",
    range: { days: 371, startDate: day(endOffset + 370), endDate: day(endOffset), totalTokens: 1200 },
    totals: { activeDays: 40, modelCalls: 90, inputTokens: 1, outputTokens: 2, sessions: 5 },
    streaks: { current: 3, longest: 9, activeToday: true },
    peakDay: { date: day(endOffset + 4), totalTokens: 700 },
    models: [{ name: "private-model", calls: 2, tokens: 3 }],
    tools: [{ name: "shell", calls: 4 }],
    days: [
      { date: day(endOffset + 1), inputTokens: 1, outputTokens: 2, totalTokens: 500, modelCalls: 3, turns: 1, toolCalls: 2, sessions: 1 },
      { date: day(endOffset), inputTokens: 1, outputTokens: 2, totalTokens: 700, modelCalls: 4, turns: 2, toolCalls: 5, sessions: 2 },
    ],
  };
}

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return { values, getItem: (key) => values.get(key) ?? null, setItem: (key, value) => { values.set(key, value); } };
}

test("only what the card reads is kept from a full answer", () => {
  const trimmed = trimHomeUsage(fullAnswer());
  assert.deepEqual(Object.keys(trimmed).sort(), ["days", "peakDay", "range", "streaks", "totals"]);
  assert.deepEqual(trimmed.totals, { activeDays: 40, modelCalls: 90 });
  assert.deepEqual(trimmed.streaks, { current: 3 });
  assert.deepEqual(Object.keys(trimmed.days[0]).sort(), ["date", "modelCalls", "totalTokens"]);
  assert.ok(!JSON.stringify(trimmed).includes("private-model"), "model and tool names stay out of storage");
});

test("a card with no peak day is stored as it is", () => {
  const answer = { ...fullAnswer(), peakDay: null };
  assert.equal(trimHomeUsage(answer).peakDay, null);
});

test("answers that are not usable are refused rather than stored", () => {
  assert.equal(trimHomeUsage(null), null);
  assert.equal(trimHomeUsage("text"), null);
  assert.equal(trimHomeUsage({ ...fullAnswer(), days: [] }), null);
  assert.equal(trimHomeUsage({ ...fullAnswer(), range: { startDate: "yesterday", endDate: day(0), totalTokens: 1 } }), null);
  assert.equal(trimHomeUsage({ ...fullAnswer(), totals: { activeDays: "many", modelCalls: 1 } }), null);
  assert.equal(trimHomeUsage({ ...fullAnswer(), days: [{ date: day(0), totalTokens: NaN, modelCalls: 1 }] }), null);
});

test("the last answer is shown again on the next launch", () => {
  const storage = memoryStorage();
  storeHomeUsage(storage, trimHomeUsage(fullAnswer()));
  assert.ok(storage.values.has(HOME_USAGE_STORAGE_KEY));
  assert.deepEqual(readStoredHomeUsage(storage, NOW), trimHomeUsage(fullAnswer()));
});

test("nothing stored, damaged storage and unavailable storage all mean a skeleton", () => {
  assert.equal(readStoredHomeUsage(memoryStorage(), NOW), null);
  assert.equal(readStoredHomeUsage(memoryStorage({ [HOME_USAGE_STORAGE_KEY]: "{not json" }), NOW), null);
  assert.equal(readStoredHomeUsage(memoryStorage({ [HOME_USAGE_STORAGE_KEY]: JSON.stringify({ range: {} }) }), NOW), null);
  assert.equal(readStoredHomeUsage(null, NOW), null);
  assert.equal(readStoredHomeUsage({ getItem() { throw new Error("blocked"); } }, NOW), null);
});

test("an answer too old to look current is not put on screen", () => {
  const recent = memoryStorage();
  storeHomeUsage(recent, trimHomeUsage(fullAnswer(HOME_USAGE_MAX_AGE_DAYS)));
  assert.ok(readStoredHomeUsage(recent, NOW), "exactly at the limit still counts");

  const old = memoryStorage();
  storeHomeUsage(old, trimHomeUsage(fullAnswer(HOME_USAGE_MAX_AGE_DAYS + 1)));
  assert.equal(readStoredHomeUsage(old, NOW), null);
});

test("a storage that refuses writes does not break the card", () => {
  assert.doesNotThrow(() => storeHomeUsage({ setItem() { throw new Error("quota"); } }, trimHomeUsage(fullAnswer())));
  assert.doesNotThrow(() => storeHomeUsage(null, trimHomeUsage(fullAnswer())));
});

// ---- how the pieces are wired ------------------------------------------------------

const card = readFileSync(new URL("../../src/components/HomeTokenActivity.tsx", import.meta.url), "utf8");
const transcript = readFileSync(new URL("../../src/components/Transcript.tsx", import.meta.url), "utf8");
const sidebar = readFileSync(new URL("../../src/components/Sidebar.tsx", import.meta.url), "utf8");
const app = readFileSync(new URL("../../src/App.tsx", import.meta.url), "utf8");

test("the card starts from the stored answer and does not read history until launch has finished", () => {
  assert.match(card, /cachedInsights \?\? readStoredHomeUsage\(homeUsageStorage\(\)\)/);
  assert.match(card, /if \(!ready \|\| cachedInsights\) return;/);
  assert.match(card, /READ_AFTER_READY_MS/);
  assert.match(card, /if \(trimmed\) storeHomeUsage\(homeUsageStorage\(\), trimmed\)/);
  // A failed refresh keeps the stored answer on screen.
  assert.match(card, /if \(failed && !data\) return null;/);
});

test("the home screen gives the card its readiness and shows launch progress", () => {
  assert.match(transcript, /<HomeTokenActivity onOpenInsights=\{onOpenInsights\} ready=\{usageReady && !connecting\} \/>/);
  assert.match(transcript, /\{connecting \? <StartupStatus stage=\{startupStage\} \/> : null\}/);
  assert.match(app, /usageReady=\{loom\.connection === "ready" && !loom\.openingThreadId\}/);
  assert.match(app, /startupStage=\{loom\.startupStage\}/);
});

test("the sidebar shows placeholders, not 'No conversations yet', while the list loads", () => {
  assert.match(sidebar, /const showLoading = Boolean\(loading\) && threads\.length === 0;/);
  assert.match(sidebar, /\{!showLoading && projectsSupported && threadView !== "archived"/);
  assert.match(sidebar, /\{!showLoading && \(!projectsSupported \|\| threadView === "archived"\)/);
  assert.match(sidebar, /compact-sidebar-skeleton/);
  assert.match(app, /loading=\{connecting\}/);
});
