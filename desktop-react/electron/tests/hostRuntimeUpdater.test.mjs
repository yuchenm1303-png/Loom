import assert from "node:assert/strict";
import { after, mock, test } from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";

// The runtime updater is Windows-only. Exercise its real async state machine
// without downloading a release or invoking PowerShell against user data.
const temp = fs.mkdtempSync(path.join(os.tmpdir(), "loom-update-test-"));
after(() => fs.rmSync(temp, { recursive: true, force: true }));
const bytes = Buffer.from("verified test archive");
const manifest = { schema: 1, version: "1.0.2", protocol: 1, platform: "win32", arch: "x64" };
const commands = [];
let failExtraction = false;
mock.module("node:child_process", { namedExports: { execFile: (command, args, options, callback) => {
  commands.push(command);
  setTimeout(() => {
    if (command === "powershell.exe") {
      if (failExtraction) return callback(new Error("archive extraction failed"));
      const destination = args.at(-1);
      fs.mkdirSync(path.join(destination, "browser-current-tab"), { recursive: true });
      fs.writeFileSync(path.join(destination, "manifest.json"), JSON.stringify(manifest));
      fs.writeFileSync(path.join(destination, "wxc-exec.exe"), "fixture");
      fs.writeFileSync(path.join(destination, "browser-current-tab/manifest.json"), "{}");
    }
    callback(null, "ok", "");
  }, 50);
} } });
mock.module("electron", { namedExports: {
  app: { isPackaged: true, getPath: () => temp, getVersion: () => "0.1.38", whenReady: () => ({ then() {} }), on() {} },
  net: { fetch: async (url) => new Response(url.includes("stable.json") ? JSON.stringify({
    ...manifest, channel: "stable", minBootstrapVersion: "0.1.11", url: "https://github.com/yuchenm1303-png/Loom/releases/download/host-v1.0.2/runtime.zip",
    sha256: crypto.createHash("sha256").update(bytes).digest("hex"), size: bytes.length,
  }) : bytes) },
} });
mock.module("../../dist-electron/hostProcess.js", { namedExports: { isHostProcess: true } });
const { compareHostRuntimeBuilds } = await import("../../dist-electron/hostRuntime.js");
mock.module("../../dist-electron/hostRuntime.js", { namedExports: {
  compareHostRuntimeBuilds,
  currentHostRuntime: () => ({ ...manifest, version: "1.0.1" }),
  hostRuntimeManagerRoot: () => temp, hostRuntimeVersionsRoot: () => path.join(temp, "versions"),
  readHostRuntimeManifest: (root) => JSON.parse(fs.readFileSync(path.join(root, "manifest.json"))),
  activateHostRuntime() { throw new Error("activation should wait for readiness guard"); },
  restoreHostRuntimePointer() {},
} });
const updater = await import("../../dist-electron/hostRuntimeUpdater.js");

test("extract and self-test yield to Host IPC and preserve extraction errors", { skip: process.platform !== "win32" }, async () => {
  try {
    let ticks = 0;
    const timer = setInterval(() => ticks++, 5);
    try {
      const result = await updater.ensureHostRuntimeUpdate();
      assert.equal(result.phase, "ready", result.error);
      assert.ok(ticks >= 2, "Host event loop remains responsive during updater subprocesses");
      assert.equal(commands[0], "powershell.exe");
      assert.equal(path.basename(commands[1]), "python.exe");
    } finally { clearInterval(timer); }
    failExtraction = true;
    const failed = await updater.ensureHostRuntimeUpdate();
    assert.equal(failed.phase, "error");
    assert.match(failed.error, /archive extraction failed/);
  } finally { fs.rmSync(temp, { recursive: true, force: true }); }
});

test("candidate smoke isolation disables online runtime downloads", async () => {
  const saved = process.env.LOOM_DISABLE_AUTO_UPDATES;
  process.env.LOOM_DISABLE_AUTO_UPDATES = "1";
  try {
    const isolated = await import("../../dist-electron/hostRuntimeUpdater.js?candidate-isolation");
    const count = commands.length;
    assert.equal((await isolated.ensureHostRuntimeUpdate()).phase, "disabled");
    assert.equal(commands.length, count);
  } finally {
    if (saved === undefined) delete process.env.LOOM_DISABLE_AUTO_UPDATES;
    else process.env.LOOM_DISABLE_AUTO_UPDATES = saved;
  }
});
