import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DESKTOP_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(DESKTOP_ROOT, "..");
const VENV_DIR = path.join(REPO_ROOT, ".venv");
const VENV_PYTHON = process.platform === "win32"
  ? path.join(VENV_DIR, "Scripts", "python.exe")
  : path.join(VENV_DIR, "bin", "python");
const BOOTSTRAP_PYTHON =
  process.env.LOOM_BOOTSTRAP_PYTHON ||
  process.env.PYTHON ||
  (process.platform === "win32" ? "python" : "python3");

function run(command, args, options = {}) {
  console.log(`[setup-python] ${command} ${args.join(" ")}`);
  const result = spawnSync(command, args, {
    cwd: REPO_ROOT,
    env: process.env,
    stdio: "inherit",
    shell: false,
    ...options,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}

if (!fs.existsSync(VENV_PYTHON)) {
  run(BOOTSTRAP_PYTHON, ["-m", "venv", VENV_DIR]);
}

run(VENV_PYTHON, ["-m", "pip", "install", "--upgrade", "pip"]);
run(VENV_PYTHON, ["-m", "pip", "install", "-e", ".[desktop-agent]"]);

console.log(`[setup-python] Ready: ${VENV_PYTHON}`);
console.log("[setup-python] npm run dev:ready will launch Electron with this interpreter.");
