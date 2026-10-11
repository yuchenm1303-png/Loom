import { test, mock } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { EventEmitter } from "node:events";
const calls = [], runtime = path.resolve("selected-host-runtime");
const result = JSON.stringify({ ok: true, result: { profiles: [] } });
mock.module("../../dist-electron/hostRuntime.js", { namedExports: {
  currentHostRuntime: () => ({ root: runtime }), resolveHostPythonExecutable: () => path.join(runtime, "python.exe"),
} });
mock.module("node:child_process", { namedExports: {
  spawnSync(executable, args, options) { calls.push({ executable, args, options }); return { status: 0, stdout: result }; },
  spawn(executable, args, options) {
    calls.push({ executable, args, options });
    const child = new EventEmitter();
    child.stdout = new EventEmitter(); child.stderr = new EventEmitter();
    child.stdout.setEncoding = child.stderr.setEncoding = () => {};
    child.stdin = { end() { process.nextTick(() => { child.stdout.emit("data", result); child.emit("close", 0); }); } };
    return child;
  },
} });
const { DesktopModelManager } = await import("../../dist-electron/modelManager.js");
test("sync and async bridges execute the selected runtime script, independent of downloaded desktop location", async () => {
  const manager = new DesktopModelManager(path.resolve("downloaded-application"));
  manager.runPythonBridge("loom_model_bridge.py", "list", {});
  await manager.runPythonBridgeAsync("loom_model_bridge.py", "list", {});
  assert.equal(calls.length, 2);
  for (const call of calls) {
    assert.equal(call.args[0], path.join(runtime, "loom_model_bridge.py"));
    assert.equal(call.options.cwd, runtime);
    assert.equal(call.executable, path.join(runtime, "python.exe"));
  }
});
