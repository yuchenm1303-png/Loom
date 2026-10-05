import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { test } from "node:test";
import { CLIENT_TIMEOUT_MS, waitForSmokeProcess } from "../host-smoke-process.mjs";

function processWith(code) {
  return spawn(process.execPath, ["-e", code], { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
}

test("smoke watchdog allows a cold client beyond the old 25-second deadline", async () => {
  assert.ok(CLIENT_TIMEOUT_MS > 120_000, "allow Host IPC deadline plus Electron startup");
  const child = processWith('setTimeout(() => console.log("HOST_CLIENT_OK"), 26_000)');
  try {
    assert.match(await waitForSmokeProcess(child, { label: "cold client" }), /HOST_CLIENT_OK/);
  } finally { child.kill(); }
});

test("hung client reports the deadline and output rather than a null-exit assertion", async () => {
  const child = processWith('console.error("initializing runtime"); setInterval(() => {}, 1000)');
  try {
    await assert.rejects(waitForSmokeProcess(child, { label: "hung client", timeoutMs: 1500 }),
      /hung client timed out after 1500ms[\s\S]*initializing runtime/);
  } finally { child.kill(); }
});

test("real process failure preserves exit code and stderr", async () => {
  const child = processWith('console.error("runtime import failed"); process.exit(7)');
  await assert.rejects(waitForSmokeProcess(child, { label: "broken client" }),
    /broken client exited \(code=7, signal=none\)[\s\S]*runtime import failed/);
});

test("executable launch failure rejects immediately", async () => {
  const child = spawn("loom-nonexistent-smoke-executable", [], { windowsHide: true });
  await assert.rejects(waitForSmokeProcess(child, { label: "missing client" }), /missing client:.*ENOENT/);
});
