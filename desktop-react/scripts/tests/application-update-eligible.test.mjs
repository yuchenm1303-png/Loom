import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
const script = fileURLToPath(new URL("../application-update-eligible.mjs", import.meta.url));
test("business edits bypass installers, while changed loader/dependencies wait for a new release baseline", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "loom-app-route-"));
  const desktop = path.join(root, "desktop-react");
  const git = args => execFileSync("git", args, { cwd: root, stdio: "ignore" });
  const run = () => execFileSync(process.execPath, [script], { cwd: desktop, encoding: "utf8", env: { ...process.env, GITHUB_OUTPUT: "" } });
  const commit = message => { git(["add", "."]); git(["commit", "-m", message]); };
  try {
    await fs.mkdir(path.join(desktop, "electron"), { recursive: true });
    await fs.writeFile(path.join(desktop, "package.json"), JSON.stringify({ version: "0.1.82" }));
    await fs.writeFile(path.join(desktop, "electron/entry.ts"), "stable loader");
    git(["init"]); git(["config", "user.name", "Release test"]); git(["config", "user.email", "release-test@example.invalid"]);
    commit("initial source");
    assert.match(run(), /Wait for/);
    git(["tag", "v0.1.82"]);
    assert.match(run(), /Publish application/);
    await fs.writeFile(path.join(desktop, "electron/modelManager.ts"), "new default model");
    commit("change model default");
    assert.match(run(), /Publish application/);
    await fs.writeFile(path.join(desktop, "electron/entry.ts"), "changed loader");
    commit("change bootstrap");
    assert.match(run(), /Wait for/);
    await fs.writeFile(path.join(desktop, "package.json"), JSON.stringify({ version: "0.1.83" }));
    commit("chore(release): Loom v0.1.83");
    assert.match(run(), /Publish application/);
  } finally { await fs.rm(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 }); }
});
