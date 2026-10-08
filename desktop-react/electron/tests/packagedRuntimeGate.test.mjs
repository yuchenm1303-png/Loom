import assert from "node:assert/strict";
import { mock, test } from "node:test";

const calls = [];
let rejectedProvider = "";
mock.module("node:child_process", { namedExports: { spawnSync(executable, args, options) {
  const provider = args[args.indexOf("--provider") + 1];
  calls.push({ executable, provider, options });
  if (provider === rejectedProvider) return { status: 1, stdout: "", stderr: "unsupported adapter" };
  return { status: 0, stdout: '{"id":1,"result":{}}\n{"id":2,"result":{"threads":[]}}\n', stderr: "" };
} } });
const { verifyPackagedRuntime } = await import("../../scripts/verify-packaged-runtime.mjs");

test("final package gate covers every desktop adapter and rejects a stale OpenCode runtime", () => {
  verifyPackagedRuntime("C:/test/resources");
  assert.deepEqual(calls.map(call => call.provider), ["openai", "openai-compatible", "opencode-go"]);
  assert.ok(calls.every(call => call.executable.endsWith("python.exe") && call.options.windowsHide));
  rejectedProvider = "opencode-go";
  assert.throws(() => verifyPackagedRuntime("C:/test/resources"), /opencode-go packaged startup failed/);
});
