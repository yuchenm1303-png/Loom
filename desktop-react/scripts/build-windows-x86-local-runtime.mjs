import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { hostRuntimeIdentity } from "./host-runtime-identity.mjs";

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repo = path.resolve(desktop, "..");
const venv = path.join(desktop, ".packaging-venv-x86");
const python = path.join(venv, "Scripts", "python.exe");
const dist = path.join(desktop, "runtime-dist-x86");
const work = path.join(desktop, "runtime-build-x86");
const interpreter = process.env.LOOM_BOOTSTRAP_PYTHON || process.env.PYTHON || "python";

function run(command, args, env = process.env) {
  console.log("[win32-local]", command, args.join(" "));
  const result = spawnSync(command, args, { cwd: repo, env: { ...env, PYTHONUTF8: "1" }, stdio: "inherit", windowsHide: true, timeout: 600000 });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`x86 runtime build failed (exit ${result.status}): ${command}`);
}
if (process.platform !== "win32") throw new Error("Windows is required for the x86 runtime build");
run(interpreter, ["-c", "import struct; assert struct.calcsize('P') == 4, 'Requires 32-bit Python'"]);
if (!fs.existsSync(python)) run(interpreter, ["-m", "venv", venv]);
run(python, ["-m", "pip", "install", "--upgrade", "pip", "wheel"]);
run(python, ["-m", "pip", "install", "-r", path.join(repo, "win32-local", "requirements-x86.txt")]);
// Do not install the full pyproject dependency set: pywinpty is not linkable
// on x86 at present. The core package remains the same exact source tree.
run(python, ["-m", "pip", "install", "--no-deps", "-e", "."]);
// Replace the x64 Playwright driver executable even though the Python wheel
// was installed into a 32-bit venv. Without this, browser-use crashes only on
// real 32-bit Windows while core Agent and Host smoke tests appear green.
run(process.execPath, [path.join(desktop, "scripts", "prepare-playwright-x86.mjs")]);
run(python, ["-m", "pip", "install", "pyinstaller>=6.10,<7"]);
run(python, ["-c", "from playwright.sync_api import sync_playwright; p = sync_playwright().start(); print('loom-x86-browser-driver-ok', p.chromium.name); p.stop()"]);
run(python, ["-c", "import struct, app, mcp, keyring, pyautogui, pywinauto, playwright, browser_use, cryptography; assert struct.calcsize('P') == 4; print('loom-x86-local-imports-ok')"]);

fs.rmSync(dist, { recursive: true, force: true });
fs.rmSync(work, { recursive: true, force: true });
fs.mkdirSync(dist, { recursive: true });
fs.mkdirSync(work, { recursive: true });
run(python, [
  "-m", "PyInstaller", "--noconfirm", "--clean", "--noupx",
  "--name", "python", "--distpath", dist, "--workpath", work,
  "--specpath", work, "--collect-submodules", "app",
  "--collect-data", "app", "--collect-all", "browser_use",
  "--collect-all", "keyring", "--collect-all", "pywinauto",
  "--collect-all", "playwright", "--collect-all", "jsonschema",
  "--collect-all", "pyautogui", "--collect-all", "psutil",
  "--hidden-import", "mcp.client.streamable_http",
  "--hidden-import", "keyring.backends.Windows",
  "--hidden-import", "win32timezone",
  path.join(repo, "loom_desktop_runtime.py"),
]);
const runtimeRoot = path.join(dist, "python");
const exe = path.join(runtimeRoot, "python.exe");
if (!fs.existsSync(exe)) throw new Error("32-bit frozen local runtime was not produced");
run(exe, ["self-test"]);
const manifest = hostRuntimeIdentity(repo, { ...process.env, LOOM_RUNTIME_ARCH: "ia32" });
fs.writeFileSync(path.join(runtimeRoot, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n", "utf8");
// A 32-bit system can run the native Agent and Windows automation without
// silently installing a 64-bit Chromium or an incompatible MXC binary.
// Browser Use requiring Chromium and MXC sandbox features remain unavailable
// until architecture-specific validated integrations exist.
console.log("[win32-local] frozen Python and local Host manifest ready", exe);
