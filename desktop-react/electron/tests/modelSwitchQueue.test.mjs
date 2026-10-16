import assert from "node:assert/strict";
import { test } from "node:test";
import { ModelSwitchQueue } from "../../dist-electron/modelSwitchQueue.js";
test("model choices stay ordered per thread while other threads stay independent", async () => {
  const queue = new ModelSwitchQueue(), events = [];
  let release;
  const blocked = new Promise(resolve => { release = resolve; });
  const first = queue.run("one", async () => { events.push("first"); await blocked; events.push("finished"); });
  const second = queue.run("one", async () => { events.push("second"); });
  await queue.run("two", async () => { events.push("independent"); });
  assert.deepEqual(events, ["first", "independent"]);
  release();
  await Promise.all([first, second]);
  assert.deepEqual(events, ["first", "independent", "finished", "second"]);
  await assert.rejects(queue.run("one", async () => { throw new Error("policy unavailable"); }));
  assert.equal(await queue.run("one", async () => "recovered"), "recovered");
});

test("slow acknowledgements cannot replace a newer preference from another conversation", async () => {
  const queue = new ModelSwitchQueue();
  const old = queue.preference(), latest = queue.preference();
  const writes = [];
  await queue.commitPreference(latest, async () => { writes.push("latest"); });
  await queue.commitPreference(old, async () => { writes.push("old-late"); });
  assert.deepEqual(writes, ["latest"]);
});

test("preference persistence is serialized independently of conversation execution", async () => {
  const queue = new ModelSwitchQueue(), writes = [];
  let release, entered;
  const started = new Promise(resolve => { entered = resolve; });
  const blocked = new Promise(resolve => { release = resolve; });
  const a = queue.preference();
  const first = queue.commitPreference(a, async () => { entered(); await blocked; writes.push("disk-A"); if (a.isCurrent()) writes.push("view-A"); });
  await started;
  const b = queue.preference();
  const second = queue.commitPreference(b, async () => { writes.push("disk-B", "view-B"); });
  release();
  await Promise.all([first, second]);
  assert.deepEqual(writes, ["disk-A", "disk-B", "view-B"]);
});
