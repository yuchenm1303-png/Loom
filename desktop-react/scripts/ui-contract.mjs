import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../", import.meta.url));
export function uiContract() {
  // Hash working-tree bytes, not commit ids: UI-only commits remain compatible.
  // Changes to Electron, dependencies, native packaging or Host sources invalidate the contract.
  const files = execFileSync("git", ["ls-files", "-z", "--", "app", "extensions", "host-runtime", "scripts", "services",
    "win32-local", "pyproject.toml", "requirements*.txt", "desktop-react/electron", "desktop-react/build",
    "desktop-react/scripts", "desktop-react/package.json", "desktop-react/package-lock.json"], { cwd: root }).toString().split("\0").filter(Boolean).sort();
  const hash = crypto.createHash("sha256");
  for (const file of files) {
    const raw = fs.readFileSync(path.join(root, file));
    const bytes = /\.(?:ts|cts|mjs|json|toml|py|ps1|txt|yml|yaml|js|cjs|nsh|spec|html|css|md)$/.test(file)
      ? Buffer.from(raw.toString("utf8").replace(/\r\n/g, "\n")) : raw;
    hash.update(file + "\0" + bytes.length + "\0"); hash.update(bytes);
  }
  return hash.digest("hex");
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  fs.writeFileSync(new URL("../dist-electron/ui-contract.json", import.meta.url), JSON.stringify({ contract: uiContract() }));
}
