import { execFileSync } from "node:child_process";
import fs from "node:fs/promises";
const sha = execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim();
const sourceTime = Number(execFileSync("git", ["show", "-s", "--format=%ct", "HEAD"], { encoding: "utf8" }).trim());
await fs.writeFile("dist/ui-baseline.json", JSON.stringify({ sourceSha: sha, sourceTime }));
