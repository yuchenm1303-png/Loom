import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import https from "node:https";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const UFO_VERSION = "3.0.8";
const UFO_TAG = "v3.0.8";
const UFO_COMMIT = "96983c73ed09e884a5f1d7ff8936c953b234b684";
const UFO_REPOSITORY = "https://github.com/microsoft/UFO.git";
const PYTHON_VERSION = "3.10.11";
const PYTHON_INSTALLER = `python-${PYTHON_VERSION}-amd64.exe`;
const PYTHON_INSTALLER_URL = `https://www.python.org/ftp/python/${PYTHON_VERSION}/${PYTHON_INSTALLER}`;
const PYTHON_INSTALLER_MD5 = "a55e9c1e6421c84a4bd8b4be41492f51";

const loomHome = path.resolve(
  process.env.LOOM_HOME?.trim() || path.join(os.homedir(), ".loom"),
);
const runtimeRoot = path.resolve(
  process.env.LOOM_PYTHON_RUNTIME_ROOT?.trim() ||
    path.join(loomHome, "runtimes", "python", PYTHON_VERSION),
);
const runtimePython = path.join(runtimeRoot, "python.exe");
const cacheRoot = path.join(loomHome, "cache", "python", PYTHON_VERSION);
const installerPath = path.join(cacheRoot, PYTHON_INSTALLER);
const installRoot = path.resolve(
  process.env.LOOM_UFO_INSTALL_ROOT?.trim() ||
    path.join(loomHome, "drivers", "ufo", UFO_VERSION),
);
const sourceRoot = path.resolve(
  process.env.LOOM_UFO_ROOT?.trim() || path.join(installRoot, "src"),
);
export const venvRoot = path.resolve(
  process.env.LOOM_UFO_VENV_ROOT?.trim() || `${installRoot}.venv`,
);
const venvPython =
  process.platform === "win32"
    ? path.join(venvRoot, "Scripts", "python.exe")
    : path.join(venvRoot, "bin", "python");

function fail(message) {
  console.error(`[setup-ufo] ${message}`);
  console.error(`[setup-ufo] source=${sourceRoot}`);
  console.error(`[setup-ufo] venv=${venvRoot}`);
  console.error(`[setup-ufo] python-runtime=${runtimeRoot}`);
  process.exit(1);
}

function windowsAppsPath() {
  if (process.platform !== "win32") return "";
  return path.join(
    process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"),
    "Microsoft",
    "WindowsApps",
  );
}

function childEnv(extra = {}) {
  const env = { ...process.env, PYTHONUTF8: "1", ...extra };
  const apps = windowsAppsPath();
  if (apps && fs.existsSync(apps)) {
    const current = String(env.PATH || env.Path || "");
    if (!current.toLowerCase().split(path.delimiter).includes(apps.toLowerCase())) {
      env.PATH = `${apps}${path.delimiter}${current}`;
    }
  }
  return env;
}

function displayCommand(command, args = []) {
  return [command, ...args].join(" ");
}

function run(command, args, { cwd, quiet = false, env, timeout = 0 } = {}) {
  if (!quiet) console.log(`[setup-ufo] ${displayCommand(command, args)}`);
  const result = spawnSync(command, args, {
    cwd,
    env: childEnv(env),
    stdio: quiet ? ["ignore", "pipe", "pipe"] : "inherit",
    encoding: "utf8",
    shell: false,
    timeout: timeout > 0 ? timeout : undefined,
  });
  if (result.error) {
    return {
      ok: false,
      error: result.error,
      status: result.status,
      stdout: String(result.stdout || "").trim(),
      stderr: String(result.stderr || "").trim(),
    };
  }
  return {
    ok: result.status === 0,
    status: result.status,
    stdout: String(result.stdout || "").trim(),
    stderr: String(result.stderr || "").trim(),
  };
}

function commandExists(command, args = ["--version"]) {
  return run(command, args, { quiet: true, timeout: 30000 }).ok;
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

function md5OfFile(target) {
  return crypto.createHash("md5").update(fs.readFileSync(target)).digest("hex");
}

function sha256OfFile(target) {
  return crypto.createHash("sha256").update(fs.readFileSync(target)).digest("hex");
}

function addCandidate(list, command, prefix = []) {
  const text = String(command || "").trim();
  if (!text) return;
  const key = `${text}\u0000${prefix.join("\u0000")}`.toLowerCase();
  if (list.some((candidate) => candidate.key === key)) return;
  list.push({ key, command: text, prefix });
}

function addPythonExecutablesFromDirectory(list, directory) {
  try {
    if (!dirExists(directory)) return;
    const names = fs.readdirSync(directory, { withFileTypes: true });
    for (const entry of names) {
      if (!entry.isDirectory()) continue;
      if (!/^python[-_]?3\.10/i.test(entry.name) && !/^python310/i.test(entry.name)) continue;
      const executable = path.join(directory, entry.name, "python.exe");
      if (fileExists(executable)) addCandidate(list, executable);
    }
  } catch {
    // Discovery is best-effort; explicit probes below decide correctness.
  }
}

function addWhereMatches(list, executable) {
  if (process.platform !== "win32") return;
  const result = run("where.exe", [executable], { quiet: true, timeout: 15000 });
  if (!result.ok) return;
  for (const line of result.stdout.split(/\r?\n/)) {
    const candidate = line.trim();
    if (candidate.toLowerCase().endsWith(".exe") && fileExists(candidate)) addCandidate(list, candidate);
  }
}

function addPyLauncherMatches(list) {
  if (process.platform !== "win32") return;
  const result = run("py", ["-0p"], { quiet: true, timeout: 15000 });
  if (!result.ok) return;
  for (const line of result.stdout.split(/\r?\n/)) {
    if (!line.includes("3.10")) continue;
    const match = line.match(/([A-Z]:\\[^\r\n]+python(?:\.exe)?)/i);
    if (match?.[1] && fileExists(match[1])) addCandidate(list, match[1]);
  }
}

export function python310Candidates(explicit, platform = process.platform) {
  const candidates = [];
  if (explicit) {
    addCandidate(candidates, explicit);
    return candidates;
  }

  if (platform === "win32") {
    addCandidate(candidates, "py", ["-3.10"]);
    addCandidate(candidates, "python3.10");
    addCandidate(candidates, "python");

    const localAppData = process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local");
    const programFiles = process.env.PROGRAMFILES || "C:\\Program Files";
    const programFilesX86 = process.env["PROGRAMFILES(X86)"] || "C:\\Program Files (x86)";
    for (const pythonPath of [
      runtimePython,
      path.join(localAppData, "Programs", "Python", "Python310", "python.exe"),
      path.join(localAppData, "Programs", "Python", "Python310-32", "python.exe"),
      path.join(programFiles, "Python310", "python.exe"),
      path.join(programFilesX86, "Python310-32", "python.exe"),
    ]) {
      addCandidate(candidates, pythonPath);
    }
    addPythonExecutablesFromDirectory(candidates, path.join(localAppData, "Programs", "Python"));
    addPythonExecutablesFromDirectory(candidates, programFiles);
    addPythonExecutablesFromDirectory(candidates, programFilesX86);
    addPythonExecutablesFromDirectory(
      candidates,
      path.join(os.homedir(), ".pyenv", "pyenv-win", "versions"),
    );
    addPyLauncherMatches(candidates);
    addWhereMatches(candidates, "python3.10.exe");
    addWhereMatches(candidates, "python.exe");
    addCandidate(candidates, path.join(windowsAppsPath(), "python3.10.exe"));
    addCandidate(candidates, path.join(windowsAppsPath(), "python.exe"));
  } else {
    addCandidate(candidates, "python3.10");
    addCandidate(candidates, "python");
  }

  return candidates;
}

function probePython310(candidates, { verbose = false } = {}) {
  const failures = [];
  for (const candidate of candidates) {
    const probe = run(
      candidate.command,
      [
        ...candidate.prefix,
        "-c",
        "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
      ],
      { quiet: true, timeout: 30000 },
    );
    if (probe.ok && probe.stdout === "3.10") {
      if (verbose) console.log(`[setup-ufo] Python 3.10 found: ${displayCommand(candidate.command, candidate.prefix)}`);
      return candidate;
    }
    if (verbose) {
      const detail = probe.ok
        ? `reported ${probe.stdout || "empty"}`
        : `not usable (${probe.error?.code || probe.status || "spawn failed"})`;
      failures.push(`${displayCommand(candidate.command, candidate.prefix)} -> ${detail}`);
    }
  }
  if (verbose && failures.length) {
    console.log("[setup-ufo] Python 3.10 probe report:");
    for (const line of failures.slice(0, 30)) console.log(`[setup-ufo]   ${line}`);
    if (failures.length > 30) console.log(`[setup-ufo]   ... ${failures.length - 30} more candidate(s)`);
  }
  return null;
}

function powershellCandidates() {
  if (process.platform !== "win32") return [];
  const systemRoot = process.env.SYSTEMROOT || "C:\\Windows";
  return [
    "pwsh.exe",
    "powershell.exe",
    path.join(systemRoot, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
  ];
}

function powershellCommand(command) {
  for (const executable of powershellCandidates()) {
    const result = run(
      executable,
      ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
      { quiet: true, timeout: 30000 },
    );
    if (result.ok && result.stdout.trim()) return result.stdout.trim();
  }
  return "";
}

function wingetCandidates() {
  const candidates = [];
  addCandidate(candidates, "winget");
  addCandidate(candidates, path.join(windowsAppsPath(), "winget.exe"));
  const resolved = powershellCommand("$cmd = Get-Command winget -ErrorAction SilentlyContinue; if ($cmd) { $cmd.Source }");
  if (resolved) addCandidate(candidates, resolved);
  return candidates;
}

function resolveWinget() {
  if (process.platform !== "win32") return null;
  for (const candidate of wingetCandidates()) {
    const probe = run(candidate.command, [...candidate.prefix, "--version"], {
      quiet: true,
      timeout: 30000,
    });
    if (probe.ok) {
      console.log(`[setup-ufo] winget found: ${displayCommand(candidate.command, candidate.prefix)} (${probe.stdout || "version unknown"})`);
      return candidate;
    }
  }
  console.log("[setup-ufo] winget was not found in PATH, WindowsApps, or PowerShell Get-Command.");
  return null;
}

function fetchUrl(url, target, redirects = 0) {
  return new Promise((resolve, reject) => {
    const request = https.get(url, { headers: { "User-Agent": "Loom-UFO-Setup" } }, (response) => {
      const status = Number(response.statusCode || 0);
      if ([301, 302, 303, 307, 308].includes(status) && response.headers.location && redirects < 5) {
        response.resume();
        const next = new URL(response.headers.location, url).toString();
        fetchUrl(next, target, redirects + 1).then(resolve, reject);
        return;
      }
      if (status !== 200) {
        response.resume();
        reject(new Error(`download failed with HTTP ${status}`));
        return;
      }
      const out = fs.createWriteStream(target);
      response.pipe(out);
      out.on("finish", () => out.close(resolve));
      out.on("error", reject);
    });
    request.setTimeout(15 * 60 * 1000, () => {
      request.destroy(new Error("download timed out"));
    });
    request.on("error", reject);
  });
}

async function ensurePythonInstallerDownloaded() {
  fs.mkdirSync(cacheRoot, { recursive: true });
  if (fileExists(installerPath)) {
    const digest = md5OfFile(installerPath);
    if (digest === PYTHON_INSTALLER_MD5) return true;
    console.warn(`[setup-ufo] Cached Python installer checksum mismatch (${digest}); re-downloading.`);
    fs.rmSync(installerPath, { force: true });
  }
  console.log(`[setup-ufo] Downloading Python ${PYTHON_VERSION} runtime installer from python.org.`);
  console.log(`[setup-ufo] ${PYTHON_INSTALLER_URL}`);
  try {
    await fetchUrl(PYTHON_INSTALLER_URL, installerPath);
  } catch (error) {
    console.warn(`[setup-ufo] python.org download blocked: ${error instanceof Error ? error.message : String(error)}`);
    fs.rmSync(installerPath, { force: true });
    return false;
  }
  const digest = md5OfFile(installerPath);
  if (digest !== PYTHON_INSTALLER_MD5) {
    console.warn(`[setup-ufo] installer checksum mismatch: expected MD5 ${PYTHON_INSTALLER_MD5}, got ${digest}`);
    fs.rmSync(installerPath, { force: true });
    return false;
  }
  return true;
}

async function installPrivatePythonRuntime() {
  if (process.platform !== "win32") return false;
  if (String(process.env.LOOM_UFO_AUTO_INSTALL_PYTHON || "1").trim() === "0") {
    console.log("[setup-ufo] Automatic Python install is disabled by LOOM_UFO_AUTO_INSTALL_PYTHON=0.");
    return false;
  }
  if (fileExists(runtimePython)) return Boolean(probePython310([{ command: runtimePython, prefix: [] }]));
  if (!(await ensurePythonInstallerDownloaded())) return false;

  fs.rmSync(runtimeRoot, { recursive: true, force: true });
  fs.mkdirSync(runtimeRoot, { recursive: true });
  console.log(`[setup-ufo] Installing private Python ${PYTHON_VERSION} runtime into ${runtimeRoot}`);
  const install = run(
    installerPath,
    [
      "/quiet",
      "InstallAllUsers=0",
      `TargetDir=${runtimeRoot}`,
      "Include_launcher=0",
      "PrependPath=0",
      "Include_pip=1",
      "Include_test=0",
      "SimpleInstall=1",
    ],
    { timeout: 15 * 60 * 1000 },
  );
  if (!install.ok) {
    console.warn(`[setup-ufo] private Python install failed (${install.error?.code || install.status || "unknown status"}).`);
    return false;
  }
  return Boolean(probePython310([{ command: runtimePython, prefix: [] }], { verbose: true }));
}

function installPythonWithWinget() {
  if (process.platform !== "win32") return false;
  if (String(process.env.LOOM_UFO_AUTO_INSTALL_PYTHON || "1").trim() === "0") return false;
  const winget = resolveWinget();
  if (!winget) return false;

  console.log("[setup-ufo] Python 3.10 was not found; installing Python.Python.3.10 with winget as a fallback.");
  const baseArgs = [
    ...winget.prefix,
    "install",
    "-e",
    "--id",
    "Python.Python.3.10",
    "--source",
    "winget",
    "--accept-package-agreements",
    "--accept-source-agreements",
  ];
  const attempts = [
    [...baseArgs, "--scope", "user"],
    baseArgs,
  ];
  for (const args of attempts) {
    const install = run(winget.command, args, { timeout: 15 * 60 * 1000 });
    if (install.ok) {
      console.log("[setup-ufo] winget Python 3.10 install command completed.");
      return true;
    }
    console.log(`[setup-ufo] winget attempt failed with ${install.error?.code || install.status || "unknown status"}.`);
  }
  return false;
}

export async function resolveBootstrapPython(overrides = {}) {
  const candidateFactory = overrides.candidateFactory || python310Candidates;
  const probe = overrides.probe || probePython310;
  const privateInstall = overrides.privateInstall || installPrivatePythonRuntime;
  const wingetInstall = overrides.wingetInstall || installPythonWithWinget;
  const explicit = process.env.LOOM_UFO_BOOTSTRAP_PYTHON?.trim();
  const found = probe(candidateFactory(explicit), { verbose: true });
  if (found) return found;

  if (!explicit && await privateInstall()) {
    const privateRuntime = probe(candidateFactory(""), { verbose: true });
    if (privateRuntime) return privateRuntime;
  }

  if (!explicit && wingetInstall()) {
    const afterInstall = probe(candidateFactory(""), { verbose: true });
    if (afterInstall) return afterInstall;
  }

  const guidance = explicit
    ? `The configured LOOM_UFO_BOOTSTRAP_PYTHON is not Python 3.10: ${explicit}`
    : "Python 3.10 was not found and Loom could not install a private Python runtime.";
  fail(
    guidance + " " +
      "Check network access to python.org, Windows execution policy, or set LOOM_UFO_BOOTSTRAP_PYTHON to a Python 3.10 executable.",
  );
}

function ensureUfoSourceTree() {
  fs.mkdirSync(installRoot, { recursive: true });

  let shouldClone = !fs.existsSync(sourceRoot);
  if (!shouldClone) {
    const head = run("git", ["rev-parse", "HEAD"], { cwd: sourceRoot, quiet: true, timeout: 30000 });
    if (!head.ok || head.stdout !== UFO_COMMIT) {
      console.warn(
        `[setup-ufo] Existing UFO checkout is invalid or not pinned (${head.stdout || "unknown"}); recreating ${sourceRoot}.`,
      );
      fs.rmSync(sourceRoot, { recursive: true, force: true });
      shouldClone = true;
    }
  }

  if (!shouldClone) return;
  fs.mkdirSync(path.dirname(sourceRoot), { recursive: true });
  const clone = run("git", [
    "clone",
    "--depth",
    "1",
    "--branch",
    UFO_TAG,
    UFO_REPOSITORY,
    sourceRoot,
  ], { timeout: 15 * 60 * 1000 });
  if (!clone.ok) fail(`git clone failed for ${UFO_TAG}`);

  const head = run("git", ["rev-parse", "HEAD"], { cwd: sourceRoot, quiet: true, timeout: 30000 });
  if (!head.ok || head.stdout !== UFO_COMMIT) {
    fail(`Pinned UFO verification failed. Expected ${UFO_COMMIT}, got ${head.stdout || "unknown"}.`);
  }
}

export function verifyVenvPython(targetPython = venvPython, targetRoot = venvRoot, runner = run) {
  if (!fileExists(targetPython)) return false;
  const pyVersion = runner(
    targetPython,
    ["-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
    { quiet: true, timeout: 30000 },
  );
  if (pyVersion.ok && pyVersion.stdout === "3.10") return true;
  console.warn(`[setup-ufo] Existing UFO virtual environment is invalid (${pyVersion.stdout || pyVersion.error?.code || "unknown"}); recreating it.`);
  fs.rmSync(targetRoot, { recursive: true, force: true });
  return false;
}

function ensureUfoVenv(bootstrap) {
  if (verifyVenvPython()) return;
  fs.rmSync(path.join(installRoot, "loom-ufo-install.json"), { force: true });
  fs.mkdirSync(path.dirname(venvRoot), { recursive: true });
  const create = run(bootstrap.command, [...bootstrap.prefix, "-m", "venv", venvRoot], {
    timeout: 5 * 60 * 1000,
  });
  if (!create.ok) fail("UFO venv creation failed.");
  if (!verifyVenvPython()) fail("Created UFO virtual environment, but it is not Python 3.10.");
}

function writeLoomUfoConfig() {
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
}

function writeInstallMarker() {
  const marker = {
    engine: "microsoft-ufo2",
    version: UFO_VERSION,
    tag: UFO_TAG,
    commit: UFO_COMMIT,
    sourceRoot,
    python: venvPython,
    privatePythonRuntime: runtimePython,
    requirementsSha256: sha256OfFile(path.join(sourceRoot, "requirements.txt")),
    installedAt: new Date().toISOString(),
  };
  fs.writeFileSync(
    path.join(installRoot, "loom-ufo-install.json"),
    JSON.stringify(marker, null, 2) + "\n",
    "utf8",
  );
  fs.writeFileSync(
    path.join(venvRoot, "loom-ufo-dependencies.json"),
    JSON.stringify({ commit: UFO_COMMIT, requirementsSha256: marker.requirementsSha256 }, null, 2) + "\n",
    "utf8",
  );
}

function dependenciesAreCurrent() {
  try {
    const marker = JSON.parse(fs.readFileSync(path.join(installRoot, "loom-ufo-install.json"), "utf8"));
    const venvMarker = JSON.parse(fs.readFileSync(path.join(venvRoot, "loom-ufo-dependencies.json"), "utf8"));
    const requirementsSha256 = sha256OfFile(path.join(sourceRoot, "requirements.txt"));
    if (
      marker.commit !== UFO_COMMIT ||
      path.resolve(String(marker.python || "")) !== venvPython ||
      marker.requirementsSha256 !== requirementsSha256 ||
      venvMarker.commit !== UFO_COMMIT ||
      venvMarker.requirementsSha256 !== requirementsSha256
    ) return false;
    return run(venvPython, ["-m", "pip", "check"], { quiet: true, timeout: 5 * 60 * 1000 }).ok;
  } catch {
    return false;
  }
}

async function main() {
  if (process.platform !== "win32") {
    fail("The UFO desktop driver is Windows-only. Loom Browser Use remains cross-platform.");
  }
  if (!commandExists("git")) {
    fail("Git is required to install the pinned Microsoft UFO source tree.");
  }

  fs.rmSync(path.join(installRoot, "loom-ufo-preflight.json"), { force: true });

  console.log(`[setup-ufo] source: target ${UFO_TAG} ${UFO_COMMIT.slice(0, 12)}`);
  ensureUfoSourceTree();
  console.log(`[setup-ufo] python-runtime: resolving Python ${PYTHON_VERSION}`);
  const bootstrap = await resolveBootstrapPython();
  console.log(`[setup-ufo] venv: ${venvRoot}`);
  ensureUfoVenv(bootstrap);

  const dependenciesReady = dependenciesAreCurrent();
  console.log(`[setup-ufo] dependencies: ${dependenciesReady ? "installed and validated" : "installing and validating requirements"}`);
  const install = dependenciesReady ? { ok: true } : run(venvPython, [
    "-m",
    "pip",
    "install",
    "--disable-pip-version-check",
    "-r",
    path.join(sourceRoot, "requirements.txt"),
  ], { timeout: 30 * 60 * 1000 });
  if (!install.ok) fail(`pip install requirements failed (requirements: ${path.join(sourceRoot, "requirements.txt")}; exit: ${install.error?.code || install.status || "unknown"}).`);
  const pipCheck = dependenciesReady ? { ok: true } : run(venvPython, ["-m", "pip", "check"], { quiet: true, timeout: 5 * 60 * 1000 });
  if (!pipCheck.ok) fail(`UFO dependency validation failed: ${pipCheck.stderr || pipCheck.stdout || `exit ${pipCheck.status}`}`);

  console.log("[setup-ufo] config: writing Loom UFO configuration");
  writeLoomUfoConfig();
  writeInstallMarker();

  console.log("");
  console.log(`[setup-ufo] Ready: Microsoft UFO ${UFO_TAG} (${UFO_COMMIT.slice(0, 12)})`);
  console.log(`[setup-ufo] Source: ${sourceRoot}`);
  console.log(`[setup-ufo] Python: ${venvPython}`);
  console.log(`[setup-ufo] Private Python runtime: ${runtimePython}`);
  console.log("[setup-ufo] No API secret was written to disk.");
  console.log("[setup-ufo] Loom will inject the active vision model/key into UFO at runtime.");
  console.log("[setup-ufo] Set LOOM_UFO_API_* only when you intentionally want a separate UFO model.");
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  await main();
}
