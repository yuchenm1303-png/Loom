import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { orchestrate, requestedDriverMode } from "../dev-ready.mjs";
import {
  python310Candidates,
  resolveBootstrapPython,
  verifyVenvPython,
} from "../setup-ufo.mjs";

test("main dev-ready defaults to strict UFO and auto is explicit", () => {
  assert.equal(requestedDriverMode([]), "ufo");
  assert.equal(requestedDriverMode(["--mode=auto"]), "auto");
});

test("Python candidate discovery prioritizes an explicit bootstrap runtime", () => {
  assert.deepEqual(python310Candidates("C:\\private\\python.exe", "win32").map(({ command, prefix }) => ({ command, prefix })), [
    { command: "C:\\private\\python.exe", prefix: [] },
  ]);
  assert.deepEqual(python310Candidates("", "linux").map(({ command, prefix }) => ({ command, prefix })), [
    { command: "python3.10", prefix: [] },
    { command: "python", prefix: [] },
  ]);
});

test("missing Python 3.10 bootstraps the private runtime before winget", async () => {
  delete process.env.LOOM_UFO_BOOTSTRAP_PYTHON;
  let probes = 0;
  let privateCalls = 0;
  let wingetCalls = 0;
  const resolved = await resolveBootstrapPython({
    candidateFactory: () => [{ command: "private-python", prefix: [] }],
    probe: () => (++probes === 1 ? null : { command: "private-python", prefix: [] }),
    privateInstall: async () => { privateCalls += 1; return true; },
    wingetInstall: () => { wingetCalls += 1; return true; },
  });
  assert.equal(resolved.command, "private-python");
  assert.equal(privateCalls, 1);
  assert.equal(wingetCalls, 0);
});

test("an invalid existing UFO venv is removed for recreation", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "loom-ufo-venv-test-"));
  const python = path.join(root, "Scripts", "python.exe");
  fs.mkdirSync(path.dirname(python), { recursive: true });
  fs.writeFileSync(python, "not python");
  assert.equal(verifyVenvPython(python, root, () => ({ ok: true, stdout: "3.11" })), false);
  assert.equal(fs.existsSync(root), false);
});

test("strict preflight failure never starts Electron", () => {
  const calls = [];
  const ok = orchestrate("ufo", {
    setupPython: () => calls.push("python"),
    setupUfo: () => { calls.push("ufo"); return true; },
    preflight: () => { calls.push("preflight"); return false; },
    startElectron: () => calls.push("electron"),
  });
  assert.equal(ok, false);
  assert.deepEqual(calls, ["python", "ufo", "preflight"]);
});

test("auto mode is the only path that skips strict preflight", () => {
  const calls = [];
  const ok = orchestrate("auto", {
    setupPython: () => calls.push("python"),
    setupUfo: () => { calls.push("ufo"); return true; },
    preflight: () => calls.push("preflight"),
    startElectron: () => calls.push("electron"),
  });
  assert.equal(ok, true);
  assert.deepEqual(calls, ["python", "ufo", "electron"]);
});
