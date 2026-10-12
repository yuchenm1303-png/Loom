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
