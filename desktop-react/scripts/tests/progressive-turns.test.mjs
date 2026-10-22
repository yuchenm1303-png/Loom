import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

// The hook needs React; the arithmetic that decides which blocks are mounted does not, and is where a conversation could
// silently lose turns.
const source = readFileSync(new URL("../../src/components/progressiveTurns.ts", import.meta.url), "utf8");
const pure = source.slice(source.indexOf("export const INITIAL_TURN_BLOCKS"), source.indexOf("type Pending"));
const compiled = ts.transpileModule(
  `${pure}\nexport { INITIAL_TURN_BLOCKS, SPARE_TURN_BLOCKS, IDLE_STEP };`,
  { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } },
).outputText;
const { INITIAL_TURN_BLOCKS, SPARE_TURN_BLOCKS, IDLE_STEP, mountedFrom, steppedStart, openingStart } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

const keys = (count) => Array.from({ length: count }, (_, index) => `turn-${index}`);

test("a conversation opens with its newest blocks, or with everything when it is short", () => {
  assert.equal(openingStart(keys(20)), "turn-18");
  assert.equal(openingStart(keys(5), 2), "turn-3");
  assert.equal(openingStart(keys(INITIAL_TURN_BLOCKS + SPARE_TURN_BLOCKS)), null, "barely more than the opening count: not split");
  assert.equal(openingStart(keys(INITIAL_TURN_BLOCKS + SPARE_TURN_BLOCKS + 1)), "turn-3", "one block more than that is");
  assert.equal(openingStart(keys(1)), null);
  assert.equal(openingStart([]), null);
});

test("the first mounted block is found by its key, and everything mounts when the key is gone", () => {
  const all = keys(10);
  assert.equal(mountedFrom(all, null), 0);
  assert.equal(mountedFrom(all, "turn-8"), 8);
  assert.equal(mountedFrom(all, "turn-0"), 0);
  assert.equal(mountedFrom(all, "turn-gone"), 0, "a missing key never hides turns");
  assert.equal(mountedFrom([], "turn-3"), 0);
});

test("older blocks are added a step at a time until the first one is reached", () => {
  const all = keys(10);
  let start = mountedFrom(all, openingStart(all));
  const seen = [start];
  while (start > 0) {
    start = mountedFrom(all, steppedStart(all, start, IDLE_STEP));
    seen.push(start);
  }
  assert.equal(seen[0], 10 - INITIAL_TURN_BLOCKS);
  assert.equal(seen.at(-1), 0);
  for (let index = 1; index < seen.length; index += 1) assert.ok(seen[index] < seen[index - 1], "every step mounts at least one more block");
  assert.equal(steppedStart(all, 1, 2), null, "a step past the first block lands on it");
  assert.equal(steppedStart(all, 0, 2), null);
  assert.equal(steppedStart(all, 5, 2), "turn-3");
});

test("blocks that arrive after the opening (a live turn) are mounted without a step", () => {
  const before = keys(20);
  const startKey = openingStart(before);
  const after = [...before, "turn-live"];
  assert.equal(mountedFrom(after, startKey), 18, "the opening key still marks the same block");
  assert.equal(after.slice(mountedFrom(after, startKey)).includes("turn-live"), true);
});

test("the transcript renders only the blocks from the first mounted one", () => {
  const transcript = readFileSync(new URL("../../src/components/Transcript.tsx", import.meta.url), "utf8");
  assert.match(transcript, /useProgressiveTurns\(blockKeys, threadId, scrollRef, Boolean\(running\)\)/, "a running turn pauses the backfill");
  assert.match(transcript, /turnBlocks\.slice\(firstMountedBlock\)\.map\(/);
  assert.match(transcript, /const index = firstMountedBlock \+ offset;/, "loose entries keep the key their position in the whole list gives them");
});
