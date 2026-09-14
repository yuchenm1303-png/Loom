import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DESKTOP_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(DESKTOP_ROOT, "..");
const VENV_ROOT = path.join(DESKTOP_ROOT, ".packaging-venv");
const VENV_PYTHON = path.join(VENV_ROOT, "Scripts", "python.exe");
const DIST_ROOT = path.join(DESKTOP_ROOT, "runtime-dist");
const WORK_ROOT = path.join(DESKTOP_ROOT, "runtime-build");
const ENTRYPOINT = path.join(REPO_ROOT, "loom_desktop_runtime.py");
const BOOTSTRAP_PYTHON = process.env.LOOM_BOOTSTRAP_PYTHON || process.env.PYTHON || "python";

function run(command, args, options = {}) {
  console.log(`[build-runtime] ${command} ${args.join(" ")}`);
  const result = spawnSync(command, args, {
    cwd: REPO_ROOT,
    env: { ...process.env, PYTHONUTF8: "1" },
    stdio: "inherit",
    shell: false,
    ...options,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}

if (process.platform !== "win32") {
  console.error("[build-runtime] Windows runtime packaging must run on Windows.");
  process.exit(2);
}

if (!fs.existsSync(VENV_PYTHON)) {
  run(BOOTSTRAP_PYTHON, ["-m", "venv", VENV_ROOT]);
}

run(VENV_PYTHON, ["-m", "pip", "install", "--upgrade", "pip", "wheel"]);
run(VENV_PYTHON, ["-m", "pip", "install", "-e", ".[desktop-agent]", "pyinstaller>=6.10,<7"]);

fs.rmSync(DIST_ROOT, { recursive: true, force: true });
fs.rmSync(WORK_ROOT, { recursive: true, force: true });
fs.mkdirSync(DIST_ROOT, { recursive: true });
fs.mkdirSync(WORK_ROOT, { recursive: true });

run(VENV_PYTHON, [
  "-m",
  "PyInstaller",
  "--noconfirm",
  "--clean",
  "--noupx",
  "--name",
  "python",
  "--distpath",
  DIST_ROOT,
  "--workpath",
  WORK_ROOT,
  "--specpath",
  WORK_ROOT,
  "--collect-submodules",
  "app",
  "--collect-data",
  "app",
  "--collect-all",
  "browser_use",
  "--collect-all",
  "mcp",
  "--collect-all",
  "keyring",
  "--collect-all",
  "pywinauto",
  "--hidden-import",
  "keyring.backends.Windows",
  "--hidden-import",
  "win32timezone",
  ENTRYPOINT,
]);

const runtimeExe = path.join(DIST_ROOT, "python", "python.exe");
if (!fs.existsSync(runtimeExe)) {
  console.error(`[build-runtime] Missing expected runtime executable: ${runtimeExe}`);
  process.exit(1);
}
run(runtimeExe, ["self-test"], { cwd: path.dirname(runtimeExe) });
console.log(`[build-runtime] Ready: ${runtimeExe}`);
