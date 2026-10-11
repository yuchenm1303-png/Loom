import fs from "node:fs";
import { execFileSync, spawnSync } from "node:child_process";
const version = JSON.parse(fs.readFileSync("package.json", "utf8")).version;
const tag = `v${version}`;
const exists = spawnSync("git", ["rev-parse", "--verify", `refs/tags/${tag}`], { stdio: "ignore" }).status === 0;
let eligible;
if (!exists) {
  // The first component release may run alongside the installer release that
  // introduces this dependency baseline. Other commits wait for that baseline.
  eligible = execFileSync("git", ["log", "-1", "--format=%s"], { encoding: "utf8" }).trim() === `chore(release): Loom v${version}`;
} else {
  const baseline = ["electron/entry.ts", "electron/applicationLoader.ts", "electron/applicationModuleResolver.ts",
    "electron/applicationUpdateStore.ts", "electron/rendererUpdateStore.ts", "package.json", "package-lock.json", "build"];
  const result = spawnSync("git", ["diff", "--quiet", tag, "HEAD", "--", ...baseline], { stdio: "ignore" });
  if (result.status !== 0 && result.status !== 1) throw new Error("Cannot verify application dependency baseline");
  eligible = result.status === 0;
}
if (process.env.GITHUB_OUTPUT) fs.appendFileSync(process.env.GITHUB_OUTPUT, `eligible=${eligible}\n`);
console.log(eligible ? "Publish application component update." : "Wait for the updated bootstrap installer baseline.");
