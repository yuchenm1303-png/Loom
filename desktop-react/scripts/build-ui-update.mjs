import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { uiContract } from "./ui-contract.mjs";
import { sha256, validateUiManifest } from "../dist-electron/uiUpdateStore.js";

const desktop = fileURLToPath(new URL("../", import.meta.url));
const pkg = JSON.parse(fs.readFileSync(path.join(desktop, "package.json"), "utf8"));
const build = Number(process.env.LOOM_UI_BUILD || Date.now());
const contract = uiContract();
const compiled = JSON.parse(fs.readFileSync(path.join(desktop, "dist-electron/ui-contract.json"), "utf8"));
if (compiled.contract !== contract) throw new Error("Sources changed after Electron build; rebuild before publishing");
const out = path.join(desktop, "release", `ui-${build}`);
fs.mkdirSync(out, { recursive: true });
const files = [];
function walk(directory, prefix = "") {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    const name = prefix + entry.name;
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) { walk(target, name + "/"); continue; }
    if (!entry.isFile()) throw new Error(`Unexpected UI asset type: ${name}`);
    const bytes = fs.readFileSync(target);
    const hash = sha256(bytes);
    fs.writeFileSync(path.join(out, hash), bytes);
    files.push({ path: name, size: bytes.length, sha256: hash,
      url: `https://github.com/yuchenm1303-png/Loom/releases/download/ui-${build}/${hash}` });
  }
}
walk(path.join(desktop, "dist"));
const manifest = validateUiManifest({ schema: 2, build, bootstrap: pkg.version, contract, files }, pkg.version, contract);
fs.writeFileSync(path.join(out, "stable.json"), JSON.stringify(manifest, null, 2) + "\n");
console.log(`UI update built: ${out} (${files.length} files, bootstrap ${pkg.version})`);
