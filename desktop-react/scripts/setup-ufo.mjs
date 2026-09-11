import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const UFO_VERSION = "3.0.8";
const UFO_TAG = "v3.0.8";
const UFO_COMMIT = "96983c73ed09e884a5f1d7ff8936c953b234b684";
const UFO_REPOSITORY = "https://github.com/microsoft/UFO.git";

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
const venvRoot = path.join(installRoot, ".venv");
const venvPython =
  process.platform === "win32"
    ? path.join(venvRoot, "Scripts", "python.exe")
    : path.join(venvRoot, "bin", "python");

function fail(message) {
  console.error(`[setup-ufo] ${message}`);
  process.exit(1);
}

function run(command, args, { cwd, quiet = false, env } = {}) {
  if (!quiet) console.log(`[setup-ufo] ${command} ${args.join(" ")}`);
  const result = spawnSync(command, args, {
    cwd,
    env: { ...process.env, PYTHONUTF8: "1", ...(env || {}) },
    stdio: quiet ? ["ignore", "pipe", "pipe"] : "inherit",
    encoding: "utf8",
    shell: false,
  });
  if (result.error) return { ok: false, error: result.error, stdout: "", stderr: "" };
  return {
    ok: result.status === 0,
    status: result.status,
    stdout: String(result.stdout || "").trim(),
    stderr: String(result.stderr || "").trim(),
  };
}

function commandExists(command, args = ["--version"]) {
  return run(command, args, { quiet: true }).ok;
}

function resolveBootstrapPython() {
  const explicit = process.env.LOOM_UFO_BOOTSTRAP_PYTHON?.trim();
  const candidates = explicit
    ? [{ command: explicit, prefix: [] }]
    : process.platform === "win32"
      ? [
          { command: "py", prefix: ["-3.10"] },
          { command: "python3.10", prefix: [] },
          { command: "python", prefix: [] },
        ]
      : [
          { command: "python3.10", prefix: [] },
          { command: "python", prefix: [] },
        ];

  for (const candidate of candidates) {
    const probe = run(
      candidate.command,
      [
        ...candidate.prefix,
        "-c",
        "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
      ],
      { quiet: true },
    );
    if (probe.ok && probe.stdout === "3.10") return candidate;
  }
  fail(
    "Microsoft UFO v3.0.8 is installed in a dedicated Python 3.10 environment. " +
      "Install Python 3.10 or set LOOM_UFO_BOOTSTRAP_PYTHON to a Python 3.10 executable.",
  );
}

if (process.platform !== "win32") {
  fail("The UFO desktop driver is Windows-only. Loom Browser Use remains cross-platform.");
}
if (!commandExists("git")) {
  fail("Git is required to install the pinned Microsoft UFO source tree.");
}

fs.mkdirSync(installRoot, { recursive: true });

if (!fs.existsSync(sourceRoot)) {
  fs.mkdirSync(path.dirname(sourceRoot), { recursive: true });
  const clone = run("git", [
    "clone",
    "--depth",
    "1",
    "--branch",
    UFO_TAG,
    UFO_REPOSITORY,
    sourceRoot,
  ]);
  if (!clone.ok) fail(`git clone failed for ${UFO_TAG}`);
}

const head = run("git", ["rev-parse", "HEAD"], { cwd: sourceRoot, quiet: true });
if (!head.ok) fail(`Existing UFO source is not a valid Git checkout: ${sourceRoot}`);
if (head.stdout !== UFO_COMMIT) {
  fail(
    `Refusing to run an unpinned UFO checkout. Expected ${UFO_COMMIT}, got ${head.stdout}. ` +
      `Remove ${sourceRoot} and run setup:ufo again.`,
  );
}

const bootstrap = resolveBootstrapPython();
if (!fs.existsSync(venvPython)) {
  const create = run(bootstrap.command, [...bootstrap.prefix, "-m", "venv", venvRoot]);
  if (!create.ok) fail("Failed to create the isolated UFO Python environment.");
}

const pyVersion = run(
  venvPython,
  ["-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
  { quiet: true },
);
if (!pyVersion.ok || pyVersion.stdout !== "3.10") {
  fail(`UFO virtual environment must use Python 3.10; found ${pyVersion.stdout || "unknown"}.`);
}

const install = run(venvPython, [
  "-m",
  "pip",
  "install",
  "--disable-pip-version-check",
  "-r",
  path.join(sourceRoot, "requirements.txt"),
]);
if (!install.ok) fail("Failed to install UFO's pinned dependencies.");

const configDir = path.join(sourceRoot, "config", "ufo");
const templatePath = path.join(configDir, "agents.yaml.template");
const agentsPath = path.join(configDir, "agents.yaml");
if (!fs.existsSync(templatePath)) fail(`Missing UFO agents template: ${templatePath}`);

let agents = fs.readFileSync(templatePath, "utf8");
const replacements = {
  API_TYPE: "${LOOM_UFO_API_TYPE}",
  API_BASE: "${LOOM_UFO_API_BASE}",
  API_KEY: "${LOOM_UFO_API_KEY}",
  API_MODEL: "${LOOM_UFO_API_MODEL}",
};
for (const [key, value] of Object.entries(replacements)) {
  const pattern = new RegExp(`^(\\s*)${key}:\\s*[^#\\r\\n]*(.*)$`, "gm");
  agents = agents.replace(pattern, (_line, indent, comment) => {
    const suffix = String(comment || "").trimStart();
    return `${indent}${key}: "${value}"${suffix ? ` ${suffix}` : ""}`;
  });
}
fs.writeFileSync(agentsPath, agents, "utf8");

const systemOverride = `# Loom-specific UFO² sidecar overrides.\n` +
`# Kept separate from upstream config so the pinned source remains auditable.\n` +
`MAX_RETRY: 3\n` +
`MAX_STEP: 40\n` +
`MAX_ROUND: 1\n` +
`SHOW_VISUAL_OUTLINE_ON_SCREEN: False\n` +
`MAXIMIZE_WINDOW: False\n` +
`CONTROL_BACKEND: ["uia"]\n` +
`SAFE_GUARD: True\n` +
`PRINT_LOG: False\n` +
`LOG_TO_MARKDOWN: False\n` +
`SAVE_UI_TREE: False\n` +
`SAVE_FULL_SCREEN: False\n` +
`TASK_STATUS: False\n` +
`SAVE_EXPERIENCE: "always_not"\n` +
`EVA_SESSION: False\n` +
`EVA_ROUND: False\n` +
`EVA_ALL_SCREENSHOTS: False\n` +
`ASK_QUESTION: False\n` +
`USE_MCP: True\n` +
`MCP_SERVERS_CONFIG: "config/ufo/mcp_loom.yaml"\n`;
fs.writeFileSync(path.join(configDir, "system_loom.yaml"), systemOverride, "utf8");

const mcpAllowlist = `# Loom UFO² GUI-only local MCP allowlist.\n` +
`# CommandLineExecutor and app COM executors are intentionally excluded from the first baseline.\n` +
`HostAgent:\n` +
`  default:\n` +
`    data_collection:\n` +
`      - namespace: UICollector\n` +
`        type: local\n` +
`        start_args: []\n` +
`        reset: false\n` +
`    action:\n` +
`      - namespace: HostUIExecutor\n` +
`        type: local\n` +
`        start_args: []\n` +
`        reset: false\n` +
`AppAgent:\n` +
`  default:\n` +
`    data_collection:\n` +
`      - namespace: UICollector\n` +
`        type: local\n` +
`        start_args: []\n` +
`        reset: false\n` +
`    action:\n` +
`      - namespace: AppUIExecutor\n` +
`        type: local\n` +
`        start_args: []\n` +
`        reset: false\n`;
fs.writeFileSync(path.join(configDir, "mcp_loom.yaml"), mcpAllowlist, "utf8");

const marker = {
  engine: "microsoft-ufo2",
  version: UFO_VERSION,
  tag: UFO_TAG,
  commit: UFO_COMMIT,
  sourceRoot,
  python: venvPython,
  installedAt: new Date().toISOString(),
};
fs.writeFileSync(
  path.join(installRoot, "loom-ufo-install.json"),
  JSON.stringify(marker, null, 2) + "\n",
  "utf8",
);

console.log("");
console.log(`[setup-ufo] Ready: Microsoft UFO ${UFO_TAG} (${UFO_COMMIT.slice(0, 12)})`);
console.log(`[setup-ufo] Source: ${sourceRoot}`);
console.log(`[setup-ufo] Python: ${venvPython}`);
console.log("[setup-ufo] No API secret was written to disk.");
console.log("[setup-ufo] Configure LOOM_UFO_API_MODEL and a provider key before starting Loom.");
