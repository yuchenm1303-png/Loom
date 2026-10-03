import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mock, test } from "node:test";
import os from "node:os";
import { spawnSync } from "node:child_process";

const children = [];
function fakeSpawn(command) {
  const child = new EventEmitter();
  child.pid = 123;
  child.command = command;
  child.kill = () => { child.killed = true; };
  for (const name of ["stdin", "stdout", "stderr"]) {
    const stream = new EventEmitter();
    stream.setEncoding = () => {};
    stream.end = () => {};
    stream.destroy = () => { stream.destroyed = true; };
    child[name] = stream;
  }
  children.push(child);
  return child;
}
mock.module("node:child_process", { namedExports: { spawn: fakeSpawn, spawnSync } });
mock.module("electron", { namedExports: { app: {
  isPackaged: false, isReady: () => false, getPath: () => os.tmpdir(),
} } });
const { DesktopModelManager } = await import("../../dist-electron/modelManager.js");

test("bridge timeout rejects even when a Windows Python child keeps pipes open", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const manager = new DesktopModelManager(os.tmpdir());
  const request = manager.runPythonBridgeAsync("loom_model_bridge.py", "save", {});
  const rejected = assert.rejects(request, /timed out \(save\)/);
  t.mock.timers.tick(20_000);
  await rejected;
  assert.equal(children[0].stdout.destroyed, true);
  assert.equal(children[0].stderr.destroyed, true);
  if (process.platform === "win32") assert.equal(children[1].command, "taskkill");
  else assert.equal(children[0].killed, true);
  // A late close must not change the timed-out result.
  children[0].emit("close", 0);
});
