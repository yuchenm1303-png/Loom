import { execFileSync } from "node:child_process";
import fs from "node:fs/promises";
// Docker builds receive the exact Git metadata from the deployment script.
// Normal desktop builds continue to read it from the local repository.
const sha = (process.env.LOOM_BUILD_SHA || execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" })).trim();
const sourceTime = Number(process.env.LOOM_BUILD_SOURCE_TIME || execFileSync("git", ["show", "-s", "--format=%ct", "HEAD"], { encoding: "utf8" }).trim());
await fs.writeFile("dist/ui-baseline.json", JSON.stringify({ sourceSha: sha, sourceTime }));
