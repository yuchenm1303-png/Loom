import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

const assets = resolve("dist/assets");
const bundle = readdirSync(assets).filter((name) => name.endsWith(".js"))
  .map((name) => readFileSync(resolve(assets, name), "utf8")).join("\n");
if (!bundle.includes("https://api.github.com/repos/yuchenm1303-png/Loom/releases/latest")) {
  throw new Error("Web portal is missing dynamic release metadata. Refusing to publish a stale frontend.");
}
if (!bundle.includes("Host connected. Click Open workspace to enter.")) {
  throw new Error("Web portal is missing the explicit workspace entry UI.");
}
console.log("Web portal bundle verified: dynamic releases and explicit workspace entry.");
