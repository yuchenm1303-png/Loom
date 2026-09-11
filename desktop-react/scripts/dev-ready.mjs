import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DESKTOP_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(DESKTOP_ROOT, "..");
const VENV_PYTHON = process.platform === "win32"
  ? path.join(REPO_ROOT, ".venv", "Scripts", "python.exe")
  : path.join(REPO_ROOT, ".venv", "bin", "python");
const NPM = process.platform === "win32" ? "npm.cmd" : "npm";
const UFO_VERSION = "3.0.8";
const loomHome = path.resolve(
  process.env.LOOM_HOME?.trim() || path.join(os.homedir(), ".loom"),
);
const ufoInstallRoot = path.resolve(
  process.env.LOOM_UFO_INSTALL_ROOT?.trim() ||
    path.join(loomHome, "drivers", "ufo", UFO_VERSION),
);
const ufoSourceRoot = path.resolve(
  process.env.LOOM_UFO_ROOT?.trim() || path.join(ufoInstallRoot, "src"),
);
const ufoPython = path.resolve(
  process.env.LOOM_UFO_PYTHON?.trim() ||
    path.join(
      ufoInstallRoot,
      ".venv",
      process.platform === "win32" ? path.join("Scripts", "python.exe") : path.join("bin", "python"),
    ),
);

function driverMode() {
  return String(process.env.LOOM_COMPUTER_DRIVER || "auto").trim().toLowerCase();
}

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

function runOptional(command, args) {
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
  });
  if (result.error) return { ok: false, reason: result.error.message || String(result.error) };
  return {
    ok: result.status === 0,
    reason: `exit code ${result.status ?? 1}`,
  };
}

function fileExists(target) {
  try {
    return fs.statSync(target).isFile();
  } catch {
    return false;
  }
}

function dirExists(target) {
  try {
    return fs.statSync(target).isDirectory();
  } catch {
    return false;
  }
}

function ufoLooksInstalled() {
  return (
    process.platform === "win32" &&
    dirExists(path.join(ufoSourceRoot, "ufo")) &&
    fileExists(ufoPython) &&
    fileExists(path.join(ufoSourceRoot, "config", "ufo", "agents.yaml")) &&
    fileExists(path.join(ufoSourceRoot, "config", "ufo", "system_loom.yaml")) &&
    fileExists(path.join(ufoSourceRoot, "config", "ufo", "mcp_loom.yaml"))
  );
}

function ensureUfoDriver() {
  if (process.platform !== "win32") return;
  const mode = driverMode();
  if (mode === "legacy") return;
  if (ufoLooksInstalled()) {
    console.log(`[dev-ready] UFO driver already installed at ${ufoSourceRoot}`);
    return;
  }

  console.log("[dev-ready] Microsoft UFO² driver is missing or incomplete; provisioning it now.");
  const setup = runOptional(process.execPath, [path.join(DESKTOP_ROOT, "scripts", "setup-ufo.mjs")]);
  if (setup.ok) return;

  if (mode === "ufo") {
    console.error(`[dev-ready] UFO setup failed in strict ufo mode (${setup.reason}).`);
    console.error("[dev-ready] Electron was not started, so this run cannot be mistaken for legacy Computer Use.");
    process.exit(1);
  }

  console.warn(`[dev-ready] UFO setup failed (${setup.reason}).`);
  console.warn("[dev-ready] Continuing because LOOM_COMPUTER_DRIVER=auto can fall back to legacy Computer Use.");
  console.warn("[dev-ready] For UFO acceptance testing, fix the setup error and start with LOOM_COMPUTER_DRIVER=ufo.");
}

function preflightUfoIfStrict() {
  if (process.platform !== "win32") return;
  if (driverMode() !== "ufo") return;
  console.log("[dev-ready] Strict UFO mode: running sidecar preflight before Electron startup.");
  const preflight = runOptional(process.execPath, [path.join(DESKTOP_ROOT, "scripts", "ufo-preflight.mjs")]);
  if (preflight.ok) return;
  console.error(`[dev-ready] UFO preflight failed in strict ufo mode (${preflight.reason}).`);
  console.error("[dev-ready] Electron was not started; inspect the preflight output above.");
  process.exit(1);
}

run(process.execPath, [path.join(DESKTOP_ROOT, "scripts", "setup-python.mjs")]);
ensureUfoDriver();
preflightUfoIfStrict();
run(NPM, ["run", "dev"]);
