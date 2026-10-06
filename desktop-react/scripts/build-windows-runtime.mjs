import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { hostRuntimeIdentity } from "./host-runtime-identity.mjs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DESKTOP_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(DESKTOP_ROOT, "..");
const VENV_ROOT = path.join(DESKTOP_ROOT, ".packaging-venv");
const VENV_PYTHON = path.join(VENV_ROOT, "Scripts", "python.exe");
const DIST_ROOT = path.join(DESKTOP_ROOT, "runtime-dist");
const BROWSER_CACHE_ROOT = path.join(DESKTOP_ROOT, ".cache", "playwright");
const WORK_ROOT = path.join(DESKTOP_ROOT, "runtime-build");
const ENTRYPOINT = path.join(REPO_ROOT, "loom_desktop_runtime.py");
const GENERATED_CONNECTOR_CONFIG = path.join(REPO_ROOT, "app", "connector_release_config_generated.py");
const BOOTSTRAP_PYTHON = process.env.LOOM_BOOTSTRAP_PYTHON || process.env.PYTHON || "python";

function run(command, args, options = {}) {
  console.log(`[build-runtime] ${command} ${args.join(" ")}`);
  const started = Date.now();
  const result = spawnSync(command, args, {
    cwd: REPO_ROOT,
    env: { ...process.env, PYTHONUTF8: "1" },
    stdio: "inherit",
    shell: false,
    ...options,
  });
  console.log(`[build-runtime] Step finished in ${((Date.now() - started) / 1000).toFixed(1)}s`);
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}

function cleanupGeneratedConnectorConfig() {
  try {
    fs.rmSync(GENERATED_CONNECTOR_CONFIG, { force: true });
  } catch {
    // Best-effort cleanup. The path is gitignored as a second safety boundary.
  }
}

function pythonString(value) {
  return JSON.stringify(String(value ?? ""));
}

function prepareGeneratedConnectorConfig() {
  cleanupGeneratedConnectorConfig();
  const clientId = String(process.env.LOOM_GITHUB_CLIENT_ID || "").trim();
  const clientSecret = String(process.env.LOOM_GITHUB_CLIENT_SECRET || "").trim();
  const scopes = String(process.env.LOOM_GITHUB_OAUTH_SCOPES || "repo read:org").trim() || "repo read:org";
  const callbackPath = String(process.env.LOOM_GITHUB_CALLBACK_PATH || "/oauth/github/callback").trim() || "/oauth/github/callback";

  if (!clientId && !clientSecret) {
    console.log("[build-runtime] GitHub Web OAuth release config: not provisioned; safe fallbacks remain available.");
    return false;
  }
  if (!clientId || !clientSecret) {
    console.error("[build-runtime] GitHub Web OAuth release config is incomplete: both client ID and client secret are required.");
    process.exit(2);
  }

  const content = [
    "# Generated at package time. Do not commit this file.",
    `GITHUB_CLIENT_ID = ${pythonString(clientId)}`,
    `GITHUB_CLIENT_SECRET = ${pythonString(clientSecret)}`,
    `GITHUB_OAUTH_SCOPES = ${pythonString(scopes)}`,
    `GITHUB_OAUTH_CALLBACK_PATH = ${pythonString(callbackPath)}`,
    "",
  ].join("\n");
  fs.writeFileSync(GENERATED_CONNECTOR_CONFIG, content, { encoding: "utf8", mode: 0o600 });
  console.log("[build-runtime] GitHub Web OAuth release config: provisioned for frozen runtime.");
  return true;
}

process.on("exit", cleanupGeneratedConnectorConfig);

if (process.platform !== "win32") {
  console.error("[build-runtime] Windows runtime packaging must run on Windows.");
  process.exit(2);
}

if (!fs.existsSync(VENV_PYTHON)) {
  run(BOOTSTRAP_PYTHON, ["-m", "venv", VENV_ROOT]);
}

run(VENV_PYTHON, ["-m", "pip", "install", "--upgrade", "pip", "wheel"]);
run(VENV_PYTHON, ["-m", "pip", "install", "--prefix", VENV_ROOT,
  ".[desktop-agent]", "pyinstaller>=6.10,<7", "playwright>=1.50,<2"]);

fs.rmSync(DIST_ROOT, { recursive: true, force: true });
fs.rmSync(WORK_ROOT, { recursive: true, force: true });
fs.mkdirSync(DIST_ROOT, { recursive: true });
fs.mkdirSync(WORK_ROOT, { recursive: true });
prepareGeneratedConnectorConfig();

// Download at build time, never on a customer's first browser action. Keep
// the browser payload beside the frozen runtime so Host updates include it.
const browserRoot = BROWSER_CACHE_ROOT;
// All frozen browser launch paths pass the bundled full Chrome executable,
// including headless mode. A second headless-shell payload is unused.
run(VENV_PYTHON, ["-m", "playwright", "install", "chromium", "--no-shell"], {
  env: { ...process.env, PYTHONUTF8: "1", PLAYWRIGHT_BROWSERS_PATH: browserRoot },
});

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
  "--hidden-import",
  "mcp.client.streamable_http",
  "--collect-all",
  "keyring",
  "--collect-all",
  "pywinauto",
  "--collect-all",
  "playwright",
  "--collect-all",
  "jsonschema",
  "--collect-all",
  "pyautogui",
  "--collect-all",
  "psutil",
  "--collect-all",
  "winpty",
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
fs.cpSync(browserRoot, path.join(DIST_ROOT, "python", "browsers"), { recursive: true });
const browserExe = fs.readdirSync(browserRoot).filter((name) => name.startsWith("chromium-"))
  .map((name) => path.join(browserRoot, name, "chrome-win64", "chrome.exe"))
  .find((name) => fs.existsSync(name));
if (!browserExe) throw new Error("Bundled Chromium executable is missing");
run(runtimeExe, ["self-test"], { cwd: path.dirname(runtimeExe) });
const cleanEnv = Object.fromEntries(Object.entries(process.env).filter(([name]) =>
  !/^(LOOM_|MINIMAX_|OPENAI_|DASHSCOPE_|AI_API_KEY|PYTHONPATH|PYTHONHOME)/i.test(name)));
run(runtimeExe, ["first-run-test"], {
  cwd: path.dirname(runtimeExe), env: { ...cleanEnv, PYTHONUTF8: "1" },
});
run(runtimeExe, ["-c", "import mcp; from mcp.client.streamable_http import streamable_http_client; print('loom-mcp-import-ok')"], { cwd: path.dirname(runtimeExe) });
run(runtimeExe, [
  "-c",
  "import tempfile; from pathlib import Path; import keyring.backends.Windows; from app.connector_web_oauth import WebOAuthConnectorManager; m=WebOAuthConnectorManager(Path(tempfile.mkdtemp()), environment={}); s=m.github_status(); assert s.get('id') == 'github'; assert 'connected' in s; assert 'webOAuthAvailable' in s; print('loom-connector-status-ok')",
], { cwd: path.dirname(runtimeExe) });

const hostManifest = hostRuntimeIdentity(REPO_ROOT);
fs.writeFileSync(path.join(DIST_ROOT, "python", "manifest.json"), `${JSON.stringify(hostManifest, null, 2)}\n`, "utf8");
console.log(`[build-runtime] Host manifest: ${hostManifest.version} protocol=${hostManifest.protocol}`);
console.log(`[build-runtime] Ready: ${runtimeExe}`);
