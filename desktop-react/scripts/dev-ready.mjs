import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DESKTOP_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(DESKTOP_ROOT, "..");
const VENV_PYTHON = process.platform === "win32"
  ? path.join(REPO_ROOT, ".venv", "Scripts", "python.exe")
  : path.join(REPO_ROOT, ".venv", "bin", "python");

function run(command, args, options = {}) {
  console.log(`[dev-ready] ${command} ${args.join(" ")}`);
  const result = spawnSync(command, args, {
    cwd: DESKTOP_ROOT,
    env: {
      ...process.env,
      LOOM_PYTHON: VENV_PYTHON,
      PYTHONUTF8: "1",
    },
    stdio: "inherit",
    shell: false,
    ...options,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}

if (!fs.existsSync(VENV_PYTHON)) {
  run(process.execPath, [path.join(DESKTOP_ROOT, "scripts", "setup-python.mjs")]);
}

run(process.execPath, [path.join(DESKTOP_ROOT, "node_modules", "npm", "bin", "npm-cli.js"), "run", "dev"]);
