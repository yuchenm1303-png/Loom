import assert from "node:assert/strict";
import { mock, test } from "node:test";
import path from "node:path";
import crypto from "node:crypto";

const original = process.env.LOOM_HOST_DATA_DIR;
const defaultPath = path.join(process.cwd(), "profile") + "/@loom/desktop-react";
mock.module("electron", { namedExports: { app: { getPath: () => defaultPath } } });
const digest = (value) => crypto.createHash("sha256").update(value).digest("hex");

test("default desktop profile and inherited Host profile resolve the same IPC identity", async () => {
  try {
    delete process.env.LOOM_HOST_DATA_DIR;
    const desktop = await import("../../dist-electron/hostPaths.js?desktop");
    process.env.LOOM_HOST_DATA_DIR = desktop.hostDataPath;
    const host = await import("../../dist-electron/hostPaths.js?host");
    assert.equal(desktop.hostDataPath, path.resolve(defaultPath));
    assert.equal(host.hostDataPath, desktop.hostDataPath);
    assert.equal(digest(host.hostDataPath), digest(desktop.hostDataPath));
  } finally {
    if (original === undefined) delete process.env.LOOM_HOST_DATA_DIR;
    else process.env.LOOM_HOST_DATA_DIR = original;
  }
});
