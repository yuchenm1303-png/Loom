import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Exercise real NSIS install/upgrade/uninstall with an isolated application ID,
// protocol, payload and installation directory. Never install over real Loom.
if (process.platform !== "win32") throw new Error("This smoke test requires Windows");
const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const workspace = await fs.mkdtemp(path.join(os.tmpdir(), "loom-installer-smoke-"));
const appName = `LoomInstallerSmoke-${process.pid}`;
const protocol = `loom-installer-smoke-${process.pid}`;
const unpacked = path.join(workspace, "payload");
const installDir = path.join(workspace, "installed app's files");
const unrelatedDir = path.join(workspace, "unrelated");
const output = path.join(workspace, "output");
const children = [];
let passed = false;
const dataDir = path.join(process.env.APPDATA, appName);
const sentinel = path.join(dataDir, `smoke-${process.pid}.txt`);

function run(executable, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(executable, args, { cwd: project, windowsHide: true, ...options });
    let text = "";
    child.stdout?.on("data", (chunk) => { text += chunk; });
    child.stderr?.on("data", (chunk) => { text += chunk; });
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error(`Timed out: ${executable}\n${text}`));
    }, 120_000);
    child.on("error", (error) => { clearTimeout(timer); reject(error); });
    child.on("exit", (code) => { clearTimeout(timer); resolve({ code, text }); });
  });
}

async function successful(executable, args, options) {
  const result = await run(executable, args, options);
  assert.equal(result.code, 0, result.text);
  return result.text;
}

async function findCompiler(directory) {
  for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
    const candidate = path.join(directory, entry.name);
    if (entry.isFile() && entry.name === "makensis.exe") return candidate;
    if (entry.isDirectory()) {
      const nested = await findCompiler(candidate);
      if (nested) return nested;
    }
  }
}

const quoteNsis = (value) => value.replaceAll("$", "$$").replaceAll('"', '$\\"');
const powershell = path.join(process.env.SystemRoot, "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
const compiler = await findCompiler(path.join(process.env.LOCALAPPDATA, "electron-builder", "Cache"));
assert.ok(compiler, "Run electron-builder once to download its NSIS compiler");

async function makeExecutable(destination, body, preamble = "") {
  const source = `${destination}.nsi`;
  await fs.writeFile(source, `Unicode true\nName "Installer smoke fixture"\nOutFile "${quoteNsis(destination)}"\nRequestExecutionLevel user\nSilentInstall silent\n${preamble}\nSection\n${body}\nSectionEnd\n`);
  await successful(compiler, ["/V2", "/INPUTCHARSET", "UTF8", source]);
}

async function exists(filename) {
  try { await fs.access(filename); return true; } catch { return false; }
}

async function waitFor(check, message) {
  for (let attempt = 0; attempt < 40; attempt++) {
    if (await check()) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(message);
}

async function appQuery(executablePath) {
  const source = await fs.readFile(path.join(project, "build", "processes.nsh"), "utf8");
  const query = source.match(/!define LOOM_APP_QUERY `([^`]+)`/)[1].replaceAll("$$", "$");
  return run(powershell, ["-NoProfile", "-NonInteractive", "-Command",
    `try { $p = @(${query}); if ($p.Count) { exit 0 }; exit 1 } catch { exit 2 }`],
  { env: { ...process.env, LOOM_INSTALL_APP_PATH: executablePath,
    LOOM_INSTALL_RESOURCE_PATH: `${path.join(path.dirname(executablePath), "resources")}\\` } });
}

try {
  await Promise.all([path.join(unpacked, "resources"), unrelatedDir, output].map((dir) => fs.mkdir(dir, { recursive: true })));
  await makeExecutable(path.join(unpacked, `${appName}.exe`), 'Exec \'"$EXEDIR\\resources\\LoomInstallerChild.exe"\'\nSleep 60000');
  await makeExecutable(path.join(unpacked, "resources", "LoomInstallerChild.exe"), "Sleep 60000");
  await fs.copyFile(path.join(unpacked, `${appName}.exe`), path.join(unrelatedDir, `${appName}.exe`));
  await fs.writeFile(path.join(unpacked, "version.txt"), "new payload");
  const wrapper = path.join(workspace, "include.nsh");
  await fs.writeFile(wrapper, `!define LOOM_PROTOCOL_SCHEME "${protocol}"\n!if "\${APP_INSTALLER_STORE_FILE}" != "${protocol}-updater\\installer.exe"\n!error "Smoke installer must use its own updater cache"\n!endif\n!addincludedir "${quoteNsis(path.join(project, "build"))}"\n!include "${quoteNsis(path.join(project, "build", "installer.nsh"))}"\n`);
  const config = path.join(workspace, "builder.json");
  await fs.writeFile(config, JSON.stringify({
    appId: `com.loom.installer-smoke.${process.pid}`, productName: appName, compression: "store",
    extraMetadata: { name: protocol },
    artifactName: "smoke-setup.exe", directories: { output }, publish: null,
    win: { executableName: appName },
    nsis: { include: wrapper, runAfterFinish: false, createDesktopShortcut: false, createStartMenuShortcut: false },
  }));
  await successful(process.execPath, [path.join(project, "node_modules", "electron-builder", "cli.js"),
    "--win", "nsis", "--x64", "--prepackaged", unpacked, "--publish", "never", "--config", config]);
  const setup = path.join(output, "smoke-setup.exe");
  const app = path.join(installDir, `${appName}.exe`);
  const failedQuery = path.join(workspace, "query-error.exe");
  await makeExecutable(failedQuery,
    `StrCpy $PowerShellPath "$EXEDIR\\missing-powershell.exe"\nStrCpy $INSTDIR "${quoteNsis(installDir)}"\n!insertmacro customCheckAppRunning\nSetErrorLevel 99`,
    `!include "LogicLib.nsh"\n!define BUILD_UNINSTALLER\n!define APP_EXECUTABLE_FILENAME "${appName}.exe"\n!define isUpdated "0 == 0"\n!define LOOM_PROTOCOL_SCHEME "${protocol}"\n!addincludedir "${quoteNsis(path.join(project, "build"))}"\n!include "${quoteNsis(path.join(project, "build", "installer.nsh"))}"\nVar PowerShellPath\nLangString appRunning 1033 "App running"\nLangString appClosing 1033 "Closing app"\nLangString appCannotBeClosed 1033 "Cannot close app"`);
  assert.equal((await run(failedQuery, ["/S"])).code, 2, "A failed query must abort instead of claiming an app is running or absent");
  assert.match(await fs.readFile(path.join(os.tmpdir(), `${protocol}-installer.log`), "utf8"), /check: error/);
  console.log("PASS unavailable process query returns a diagnostic error");
  assert.equal((await appQuery(app)).code, 1, "No process must mean not running");
  await successful(setup, ["/S", `/D=${installDir}`]);
  assert.equal(await fs.readFile(path.join(installDir, "version.txt"), "utf8"), "new payload");
  console.log("PASS first installation with spaces and apostrophe in path");

  const unrelated = spawn(path.join(unrelatedDir, `${appName}.exe`), [], { windowsHide: true });
  children.push(unrelated);
  await new Promise((resolve) => setTimeout(resolve, 300));
  assert.equal((await appQuery(app)).code, 1, "A same-named app elsewhere must not block installation");
  await fs.mkdir(dataDir, { recursive: true });
  await fs.writeFile(sentinel, "keep user data");
  await fs.writeFile(path.join(installDir, "obsolete.dll"), "old payload");
  await makeExecutable(path.join(installDir, `Uninstall ${appName}.exe`), "SetErrorLevel 42\nQuit");
  await successful(setup, ["/S", `/D=${installDir}`]);
  assert.equal(await exists(path.join(installDir, "obsolete.dll")), false);
  assert.equal(await fs.readFile(sentinel, "utf8"), "keep user data");
  assert.equal(unrelated.exitCode, null, "An unrelated installation must stay running");
  console.log("PASS upgrade bypasses broken legacy uninstaller, removes stale files and preserves data");

  const runningApp = spawn(app, [], { windowsHide: true });
  children.push(runningApp);
  await waitFor(async () => (await appQuery(app)).code === 0, "Fixture app did not start");
  await successful(setup, ["/S", `/D=${installDir}`]);
  await waitFor(() => runningApp.exitCode !== null, "Installer did not close the app");
  assert.equal((await appQuery(app)).code, 1);
  assert.equal(unrelated.exitCode, null);
  const childQuery = await successful(powershell, ["-NoProfile", "-NonInteractive", "-Command",
    "@(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $env:LOOM_SMOKE_CHILD }).Count"],
  { env: { ...process.env, LOOM_SMOKE_CHILD: path.join(installDir, "resources", "LoomInstallerChild.exe") } });
  assert.equal(childQuery.trim(), "0", "Host child processes must also exit");
  console.log("PASS running app and its child process stop before replacement");

  const orphan = spawn(path.join(installDir, "resources", "LoomInstallerChild.exe"), [], { windowsHide: true });
  children.push(orphan);
  await waitFor(async () => (await appQuery(app)).code === 0, "Orphan runtime did not start");
  await successful(setup, ["/S", "--updated", `/D=${installDir}`]);
  await waitFor(() => orphan.exitCode !== null, "Installer did not close the orphan runtime");
  assert.equal(unrelated.exitCode, null);
  console.log("PASS orphan bundled runtime stops without touching unrelated apps");

  await successful(path.join(installDir, `Uninstall ${appName}.exe`), ["/S"]);
  // A normal NSIS uninstall relaunches its temporary copy before exiting.
  await waitFor(async () => !(await exists(app)), "Uninstaller did not remove the app");
  assert.equal(await fs.readFile(sentinel, "utf8"), "keep user data");
  const registration = await run(path.join(process.env.SystemRoot, "System32", "reg.exe"),
    ["query", `HKCU\\Software\\Classes\\${protocol}`]);
  assert.notEqual(registration.code, 0, "Uninstall must remove its protocol");
  await fs.rm(sentinel);
  console.log("PASS uninstall cleans application and protocol, preserving user data");
  passed = true;
} finally {
  for (const child of children) {
    if (child.exitCode === null) {
      await run(path.join(process.env.SystemRoot, "System32", "taskkill.exe"), ["/F", "/T", "/PID", String(child.pid)]);
    }
  }
  const uninstaller = path.join(installDir, `Uninstall ${appName}.exe`);
  if (await exists(uninstaller)) {
    await run(uninstaller, ["/S"]);
    await waitFor(async () => !(await exists(path.join(installDir, `${appName}.exe`))), "Test cleanup did not finish");
  }
  await fs.rm(sentinel, { force: true });
  // Only empty test data folders are removed. Keep failure artifacts for diagnosis.
  try { await fs.rmdir(dataDir); } catch {}
  if (passed) {
    assert.equal(path.dirname(workspace), path.resolve(os.tmpdir()));
    assert.ok(path.basename(workspace).startsWith("loom-installer-smoke-"));
    await fs.rm(workspace, { recursive: true, force: true });
    const smokeCache = path.join(process.env.LOCALAPPDATA, `${protocol}-updater`, "installer.exe");
    await fs.rm(smokeCache, { force: true });
    try { await fs.rmdir(path.dirname(smokeCache)); } catch {}
    await fs.rm(path.join(os.tmpdir(), `${protocol}-installer.log`), { force: true });
  } else console.log(`Smoke artifacts: ${workspace}`);
}
