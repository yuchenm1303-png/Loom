import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

const assets = resolve("dist/assets");
const bundle = readdirSync(assets).filter((name) => name.endsWith(".js"))
  .map((name) => readFileSync(resolve(assets, name), "utf8")).join("\n");
if (!bundle.includes("https://api.github.com/repos/yuchenm1303-png/Loom/releases/latest")) {
  throw new Error("Web portal is missing dynamic release metadata. Refusing to publish a stale frontend.");
}
// Check the entry control, rather than presentation copy that can be revised.
if (!bundle.includes("loom-form-submit loom-host-action") || !bundle.includes("Open Loom Web")) {
  throw new Error("Web portal is missing the explicit workspace entry UI.");
}
console.log("Web portal bundle verified: dynamic releases and explicit workspace entry.");
