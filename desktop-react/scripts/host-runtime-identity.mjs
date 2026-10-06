import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Both release channels identify the same source revision the same way. Do not
// use workflow run numbers: independent workflows have independent counters.
export function hostRuntimeIdentity(repoRoot, environment = process.env) {
  const config = JSON.parse(fs.readFileSync(path.join(repoRoot, "host-runtime/runtime.json"), "utf8"));
  const sourceSha = execFileSync("git", ["rev-parse", "HEAD"], { cwd: repoRoot, encoding: "utf8" }).trim();
  const sourceEpoch = execFileSync("git", ["show", "-s", "--format=%ct", "HEAD"], { cwd: repoRoot, encoding: "utf8" }).trim();
  if (!/^\d+\.\d+$/.test(config.series) || !/^\d+$/.test(sourceEpoch)) throw new Error("Invalid Host runtime source identity");
  const version = String(environment.LOOM_HOST_RUNTIME_VERSION || `${config.series}.${sourceEpoch}`).trim();
  const protocol = Number(environment.LOOM_HOST_RUNTIME_PROTOCOL || config.protocol);
  const minBootstrapVersion = String(environment.LOOM_HOST_MIN_BOOTSTRAP_VERSION || config.minBootstrapVersion).trim();
  if (!/^\d+\.\d+\.\d+$/.test(version) || !Number.isSafeInteger(protocol) || protocol < 0
    || !/^\d+\.\d+\.\d+$/.test(minBootstrapVersion)) throw new Error("Invalid Host runtime release identity");
  return { schema: 1, version, protocol, platform: "win32", arch: "x64", minBootstrapVersion,
    sourceSha, publishedAt: new Date(Number(sourceEpoch) * 1000).toISOString() };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  console.log(JSON.stringify(hostRuntimeIdentity(path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../.."))));
}
