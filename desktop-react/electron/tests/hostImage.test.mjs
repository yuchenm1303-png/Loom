import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

const { HOST_IMAGE, hostLaunchExecutable } = await import("../../dist-electron/hostImage.js");

function runtime(withInternal = true) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "loom-host-image-"));
  const python = path.join(root, "python.exe");
  fs.writeFileSync(python, "frozen launcher bytes");
  if (withInternal) fs.mkdirSync(path.join(root, "_internal"));
  return { root, python };
}

test("the Host starts from its own image name, so ending python does not end it", () => {
  const { root, python } = runtime();
  try {
    const image = hostLaunchExecutable(python, "win32");
    assert.equal(image, path.join(root, HOST_IMAGE));
    assert.equal(fs.readFileSync(image, "utf8"), "frozen launcher bytes");
    assert.ok(fs.existsSync(python), "python.exe stays for the tools that address it by that name");
    assert.equal(hostLaunchExecutable(python, "win32"), image, "a second start reuses it");
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test("a new runtime build replaces a stale image", () => {
  const { root, python } = runtime();
  try {
    const image = hostLaunchExecutable(python, "win32");
    fs.rmSync(python);
    fs.writeFileSync(python, "a newer, longer launcher");
    assert.equal(hostLaunchExecutable(python, "win32"), image);
    assert.equal(fs.readFileSync(image, "utf8"), "a newer, longer launcher");
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test("a development virtualenv python, another platform or a missing file is left alone", () => {
  const dev = runtime(false);
  const frozen = runtime();
  try {
    assert.equal(hostLaunchExecutable(dev.python, "win32"), dev.python);
    assert.equal(hostLaunchExecutable(frozen.python, "linux"), frozen.python);
    assert.equal(hostLaunchExecutable(path.join(frozen.root, "python3.12.exe"), "win32"), path.join(frozen.root, "python3.12.exe"));
    fs.rmSync(frozen.python);
    assert.equal(hostLaunchExecutable(frozen.python, "win32"), frozen.python);
  } finally {
    fs.rmSync(dev.root, { recursive: true, force: true });
    fs.rmSync(frozen.root, { recursive: true, force: true });
  }
});
