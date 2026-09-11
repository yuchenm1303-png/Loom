import { spawnSync } from "node:child_process";
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
export function requestedDriverMode(argv = process.argv.slice(2)) {
  const option = argv.find((value) => value.startsWith("--mode="));
  const mode = option?.slice("--mode=".length).trim().toLowerCase() || "ufo";
  return mode === "auto" ? "auto" : "ufo";
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

function ensureUfoDriver(mode) {
  if (process.platform !== "win32") {
    if (mode === "ufo") {
      console.error("[dev-ready] strict UFO startup requires Windows; Electron was not started");
      return false;
    }
    return true;
  }
  console.log("[dev-ready] provisioning and repairing Microsoft UFO²");
  const setup = runOptional(process.execPath, [path.join(DESKTOP_ROOT, "scripts", "setup-ufo.mjs")]);
  if (setup.ok) return true;

  if (mode === "ufo") {
    console.error(`[dev-ready] UFO setup failed in strict ufo mode (${setup.reason}).`);
    console.error("[dev-ready] Electron was not started, so this run cannot be mistaken for legacy Computer Use.");
    return false;
  }

  console.warn(`[dev-ready] UFO setup failed (${setup.reason}).`);
  console.warn("[dev-ready] Continuing because LOOM_COMPUTER_DRIVER=auto can fall back to legacy Computer Use.");
  console.warn("[dev-ready] For UFO acceptance testing, fix the setup error and start with LOOM_COMPUTER_DRIVER=ufo.");
  return true;
}

function preflightUfoIfStrict(mode) {
  if (mode !== "ufo") return true;
  console.log("[dev-ready] Strict UFO mode: running sidecar preflight before Electron startup.");
  const preflight = runOptional(process.execPath, [path.join(DESKTOP_ROOT, "scripts", "ufo-preflight.mjs")]);
  if (preflight.ok) return true;
  console.error(`[dev-ready] UFO preflight failed in strict ufo mode (${preflight.reason}).`);
  console.error("[dev-ready] Electron was not started; inspect the preflight output above.");
  return false;
}

export function main(argv = process.argv.slice(2)) {
  const mode = requestedDriverMode(argv);
  process.env.LOOM_COMPUTER_DRIVER = mode;
  console.log(`[dev-ready] driver-mode=${mode}`);
  console.log("[dev-ready] Loom Python environment");
  const ok = orchestrate(mode, {
    setupPython: () => run(process.execPath, [path.join(DESKTOP_ROOT, "scripts", "setup-python.mjs")]),
    setupUfo: () => ensureUfoDriver(mode),
    preflight: () => preflightUfoIfStrict(mode),
    startElectron: () => run(NPM, ["run", "dev"]),
  });
  if (!ok) process.exitCode = 1;
}

export function orchestrate(mode, actions) {
  actions.setupPython();
  if (!actions.setupUfo()) return false;
  if (mode === "ufo" && !actions.preflight()) return false;
  actions.startElectron();
  return true;
}

if (path.resolve(process.argv[1] || "") === __filename) main();
