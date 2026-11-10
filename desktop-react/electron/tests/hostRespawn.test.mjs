import assert from "node:assert/strict";
import { test } from "node:test";
import { spawn } from "node:child_process";
import { EventEmitter } from "node:events";

const { observeHostExit, RESPAWN_DELAYS_MS, RESPAWN_MAX_TRIES, RespawnPolicy, STABLE_AFTER_MS } = await import("../../dist-electron/hostRespawn.js");

test("a failed spawn without exit reaches recovery", async () => {
  const child = spawn(process.execPath + "-missing-loom-host", [], { windowsHide: true });
  let error;
  child.on("error", (value) => { error = value; });
  await new Promise((resolve) => observeHostExit(child, resolve));
  assert.equal(error.code, "ENOENT");
});

test("exit followed by close schedules only one restart", () => {
  const child = new EventEmitter();
  const outcomes = [];
  observeHostExit(child, (code, signal) => outcomes.push([code, signal]));
  child.emit("exit", 1, null);
  child.emit("close", 1, null);
  assert.deepEqual(outcomes, [[1, null]]);
  assert.equal(child.listenerCount("close"), 0);
});

function clock() {
  let now = 1_000_000;
  return { now: () => now, advance: (ms) => { now += ms; } };
}

test("a Host that dies at once is restarted with growing delays, then left alone", () => {
  const time = clock();
  const policy = new RespawnPolicy(time.now);
  const delays = [];
  for (let i = 0; i < RESPAWN_MAX_TRIES; i += 1) {
    policy.started();
    time.advance(1000);
    delays.push(policy.next());
  }
  assert.deepEqual(delays.slice(0, 4), RESPAWN_DELAYS_MS.slice(0, 4));
  assert.ok(delays.every((delay, index) => index === 0 || delay >= delays[index - 1]), "never shorter than the one before");
  policy.started();
  assert.equal(policy.next(), null, "a Host that cannot stay up is a bug, not something to loop on");
});

test("a Host that stayed up for a while starts the count again", () => {
  const time = clock();
  const policy = new RespawnPolicy(time.now);
  for (let i = 0; i < 4; i += 1) { policy.started(); time.advance(500); policy.next(); }
  policy.started();
  time.advance(STABLE_AFTER_MS + 1);
  assert.equal(policy.next(), RESPAWN_DELAYS_MS[0]);
});

test("a stop on purpose clears what was held against the Host", () => {
  const time = clock();
  const policy = new RespawnPolicy(time.now);
  for (let i = 0; i < RESPAWN_MAX_TRIES; i += 1) { policy.started(); policy.next(); }
  policy.reset();
  policy.started();
  assert.equal(policy.next(), RESPAWN_DELAYS_MS[0]);
});
