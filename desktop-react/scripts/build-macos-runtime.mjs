import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { hostRuntimeIdentity } from "./host-runtime-identity.mjs";
import { verifyPackagedRuntime } from "./verify-packaged-runtime.mjs";

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = path.resolve(desktopRoot, "..");
const venv = path.join(desktopRoot, ".packaging-venv-mac");
const python = path.join(venv, "bin", "python");
const dist = path.join(desktopRoot, "runtime-dist");
const browserCache = path.join(desktopRoot, ".cache", "playwright-macos");
const work = path.join(desktopRoot, "runtime-build-mac");
const configPath = path.join(repoRoot, "app", "connector_release_config_generated.py");
const identity = hostRuntimeIdentity(repoRoot);

function run(cmd, args, env = process.env) {
  console.log("[mac-runtime]", cmd, args.join(" "));
  const result = spawnSync(cmd, args, {
    cwd: repoRoot, env: { ...env, PYTHONUTF8: "1" },
    stdio: "inherit", shell: false,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`Command failed with exit code ${result.status}: ${cmd}`);
}

if (process.platform !== "darwin" || process.arch !== "arm64") {
  throw new Error("macOS runtime must be built natively on Apple Silicon (arm64)");
}

function stageConnectorConfig() {
  fs.rmSync(configPath, { force: true });
  const id = (process.env.LOOM_GITHUB_CLIENT_ID || "").trim();
  const secret = (process.env.LOOM_GITHUB_CLIENT_SECRET || "").trim();
  if (!id && !secret) return;
  if (!id || !secret) throw new Error("GitHub OAuth build config requires client ID and secret together");
  const scopes = (process.env.LOOM_GITHUB_OAUTH_SCOPES || "repo read:org").trim();
  const callback = (process.env.LOOM_GITHUB_CALLBACK_PATH || "/oauth/github/callback").trim();
  const content = [
    "# Generated for this build only; never commit.",
    `GITHUB_CLIENT_ID = ${JSON.stringify(id)}`,
    `GITHUB_CLIENT_SECRET = ${JSON.stringify(secret)}`,
    `GITHUB_OAUTH_SCOPES = ${JSON.stringify(scopes)}`,
    `GITHUB_OAUTH_CALLBACK_PATH = ${JSON.stringify(callback)}`, "",
  ].join("\n");
  fs.writeFileSync(configPath, content, { encoding: "utf8", mode: 0o600 });
}

process.on("exit", () => fs.rmSync(configPath, { force: true }));
try {
  if (!fs.existsSync(python)) {
    run(process.env.LOOM_BOOTSTRAP_PYTHON || "python3", ["-m", "venv", venv]);
  }
  run(python, ["-m", "pip", "install", "--upgrade", "pip", "wheel"]);
  run(python, ["-m", "pip", "install", "-e", ".[desktop-agent]", "pyinstaller>=6.10,<7", "playwright>=1.50,<2"]);

  fs.rmSync(dist, { recursive: true, force: true });
  fs.rmSync(work, { recursive: true, force: true });
  fs.mkdirSync(work, { recursive: true });
  stageConnectorConfig();
  run(python, ["-m", "playwright", "install", "chromium", "--no-shell"], {
    ...process.env, PLAYWRIGHT_BROWSERS_PATH: browserCache,
  });

  run(python, [
    "-m", "PyInstaller", "--noconfirm", "--clean", "--noupx", "--onedir",
    "--name", "python", "--distpath", dist,
    "--workpath", work, "--specpath", work,
    "--collect-submodules", "app",
    "--collect-data", "app",
    "--collect-all", "browser_use",
    "--hidden-import", "mcp.client.streamable_http",
    "--collect-all", "keyring",
    "--collect-all", "pyautogui",
    "--collect-all", "playwright",
    "--collect-all", "jsonschema",
    "--collect-all", "Quartz",
    "--collect-all", "ApplicationServices",
    path.join(repoRoot, "loom_desktop_runtime.py"),
  ]);

  const runtimeDir = path.join(dist, "python");
  const executable = path.join(runtimeDir, "python");
  if (!fs.existsSync(executable)) throw new Error("Frozen macOS Python Host is missing");
  const browsers = path.join(runtimeDir, "browsers");
  fs.cpSync(browserCache, browsers, { recursive: true, force: true });
  // first-run-test checks the real Playwright Chromium executable and launch;
  // the browser layout varies by Playwright revision and macOS architecture.
  run(executable, ["self-test"]);
  run(executable, ["first-run-test"]);
  verifyPackagedRuntime(runtimeDir);
  fs.writeFileSync(path.join(runtimeDir, "manifest.json"), JSON.stringify(identity, null, 2) + "\n");
  console.log("[mac-runtime] Apple Silicon Host validated:", identity.version);
} finally {
  fs.rmSync(configPath, { force: true });
}
