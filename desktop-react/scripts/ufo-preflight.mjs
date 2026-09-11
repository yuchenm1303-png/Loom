import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const UFO_VERSION = "3.0.8";
const UFO_COMMIT = "96983c73ed09e884a5f1d7ff8936c953b234b684";
const PROTOCOL = "loom-ufo-sidecar";
const PROTOCOL_VERSION = 1;

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DESKTOP_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(DESKTOP_ROOT, "..");
const loomHome = path.resolve(
  process.env.LOOM_HOME?.trim() || path.join(os.homedir(), ".loom"),
);
const installRoot = path.resolve(
  process.env.LOOM_UFO_INSTALL_ROOT?.trim() ||
    path.join(loomHome, "drivers", "ufo", UFO_VERSION),
);
const sourceRoot = path.resolve(
  process.env.LOOM_UFO_ROOT?.trim() || path.join(installRoot, "src"),
);
const python = path.resolve(
  process.env.LOOM_UFO_PYTHON?.trim() ||
    path.join(
      installRoot,
      ".venv",
      process.platform === "win32" ? path.join("Scripts", "python.exe") : path.join("bin", "python"),
    ),
);
const sidecar = path.join(REPO_ROOT, "app", "agent_runtime", "ufo_sidecar.py");

function fail(message) {
  console.error(`[ufo-preflight] ${message}`);
  process.exit(1);
}

function requireFile(target, label) {
  if (!fs.existsSync(target) || !fs.statSync(target).isFile()) {
    fail(`${label} is missing: ${target}`);
  }
}

if (process.platform !== "win32") {
  fail("Microsoft UFO² desktop execution is Windows-only.");
}
requireFile(python, "UFO isolated Python");
requireFile(sidecar, "Loom UFO sidecar");
requireFile(path.join(sourceRoot, "config", "ufo", "agents.yaml"), "UFO Loom agent config");
requireFile(path.join(sourceRoot, "config", "ufo", "system_loom.yaml"), "UFO Loom system override");
requireFile(path.join(sourceRoot, "config", "ufo", "mcp_loom.yaml"), "UFO Loom MCP allowlist");

const input = JSON.stringify({ command: "shutdown", request_id: "loom-preflight" }) + "\n";
const result = spawnSync(
  python,
  [sidecar, "--ufo-root", sourceRoot],
  {
    cwd: sourceRoot,
    env: {
      ...process.env,
      PYTHONUTF8: "1",
      PYTHONUNBUFFERED: "1",
      UFO_ENV: "loom",
    },
    input,
    encoding: "utf8",
    stdio: ["pipe", "pipe", "pipe"],
    windowsHide: true,
    timeout: 60000,
    shell: false,
  },
);

if (result.error) {
  fail(`Could not launch UFO sidecar (${result.error.name || "spawn error"}).`);
}
if (result.status !== 0) {
  const stderrLines = String(result.stderr || "").split(/\r?\n/).filter(Boolean).length;
  fail(`UFO sidecar exited with code ${result.status}; stderr contained ${stderrLines} redacted diagnostic line(s).`);
}

const messages = [];
for (const line of String(result.stdout || "").split(/\r?\n/)) {
  if (!line.trim()) continue;
  try {
    const parsed = JSON.parse(line);
    if (parsed && typeof parsed === "object") messages.push(parsed);
  } catch {
    fail("UFO sidecar wrote non-JSON data to its protocol stdout.");
  }
}

const ready = messages.find((message) => message.type === "ready");
if (!ready) fail("UFO sidecar did not emit a ready handshake.");
if (ready.protocol !== PROTOCOL || Number(ready.protocol_version) !== PROTOCOL_VERSION) {
  fail(`UFO protocol mismatch; expected ${PROTOCOL} v${PROTOCOL_VERSION}.`);
}
if (ready.git_head !== UFO_COMMIT) {
  fail(`UFO revision mismatch; expected ${UFO_COMMIT}, got ${ready.git_head || "unknown"}.`);
}
const shutdown = messages.find(
  (message) => message.type === "shutdown" && message.request_id === "loom-preflight",
);
if (!shutdown || shutdown.ok !== true) {
  fail("UFO sidecar did not complete a clean shutdown handshake.");
}

const envModelConfigured = Boolean(
  process.env.LOOM_UFO_API_MODEL?.trim() ||
  process.env.LOOM_MODEL?.trim() ||
  process.env.AGENT_MODEL?.trim(),
);
const envKeyConfigured = Boolean(
  process.env.LOOM_UFO_API_KEY?.trim() ||
  process.env.OPENAI_API_KEY?.trim() ||
  process.env.DASHSCOPE_API_KEY?.trim() ||
  process.env.LOOM_API_KEY?.trim() ||
  process.env.AI_API_KEY?.trim(),
);

console.log(`[ufo-preflight] OK: ${PROTOCOL} v${PROTOCOL_VERSION}`);
console.log(`[ufo-preflight] UFO revision: ${String(ready.git_head).slice(0, 12)}`);
console.log(`[ufo-preflight] Transport: stdio NDJSON; no network listener`);
console.log(`[ufo-preflight] Model env configured: ${envModelConfigured ? "yes" : "no (Loom can inject the active vision model in memory)"}`);
console.log(`[ufo-preflight] Provider key env configured: ${envKeyConfigured ? "yes" : "no (Loom can inject the active provider key in memory)"}`);
