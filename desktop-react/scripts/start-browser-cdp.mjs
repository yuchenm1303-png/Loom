#!/usr/bin/env node
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DESKTOP_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(DESKTOP_ROOT, "..");
const DEFAULT_PORT = 9222;

function log(message) {
  console.log(`[browser-cdp] ${message}`);
}

function warn(message) {
  console.warn(`[browser-cdp] ${message}`);
}

function envText(name, fallback = "") {
  return String(process.env[name] || fallback).trim();
}

function flagDisabled(name) {
  return /^(?:0|false|off|no|disabled)$/i.test(envText(name));
}

function parsePort() {
  const raw = envText("LOOM_BROWSER_CDP_PORT") || String(DEFAULT_PORT);
  const value = Number.parseInt(raw, 10);
  if (!Number.isInteger(value) || value < 1 || value > 65535) {
    throw new Error(`LOOM_BROWSER_CDP_PORT must be a TCP port number, got ${raw}`);
  }
  return value;
}

function existsExecutable(candidate) {
  if (!candidate) return false;
  if (path.isAbsolute(candidate)) return fs.existsSync(candidate);
  if (process.platform === "win32") {
    const result = spawnSync("where", [candidate], { stdio: "ignore", windowsHide: true });
    return result.status === 0;
  }
  const result = spawnSync("sh", ["-lc", `command -v ${JSON.stringify(candidate)} >/dev/null 2>&1`], {
    stdio: "ignore",
  });
  return result.status === 0;
}

function windowsCandidates() {
  const programFiles = envText("PROGRAMFILES");
  const programFilesX86 = envText("PROGRAMFILES(X86)");
  const localAppData = envText("LOCALAPPDATA");
  return {
    edge: [
      path.join(programFiles, "Microsoft", "Edge", "Application", "msedge.exe"),
      path.join(programFilesX86, "Microsoft", "Edge", "Application", "msedge.exe"),
      path.join(localAppData, "Microsoft", "Edge", "Application", "msedge.exe"),
      "msedge.exe",
    ],
    chrome: [
      path.join(programFiles, "Google", "Chrome", "Application", "chrome.exe"),
      path.join(programFilesX86, "Google", "Chrome", "Application", "chrome.exe"),
      path.join(localAppData, "Google", "Chrome", "Application", "chrome.exe"),
      "chrome.exe",
    ],
  };
}

function darwinCandidates() {
  return {
    edge: [
      "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
      path.join(envText("HOME"), "Applications", "Microsoft Edge.app", "Contents", "MacOS", "Microsoft Edge"),
    ],
    chrome: [
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
      path.join(envText("HOME"), "Applications", "Google Chrome.app", "Contents", "MacOS", "Google Chrome"),
    ],
  };
}

function linuxCandidates() {
  return {
    edge: ["microsoft-edge", "microsoft-edge-stable", "msedge"],
    chrome: ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"],
  };
}

function candidateMap() {
  if (process.platform === "win32") return windowsCandidates();
  if (process.platform === "darwin") return darwinCandidates();
  return linuxCandidates();
}

function preferredOrder() {
  const preferred = envText("LOOM_DESKTOP_BROWSER") || envText("LOOM_BROWSER_ENGINE") || "edge";
  if (/^chrome$/i.test(preferred)) return ["chrome", "edge"];
  if (/^edge$/i.test(preferred)) return ["edge", "chrome"];
  return ["edge", "chrome"];
}

function findBrowserExecutable() {
  const configured = envText("LOOM_BROWSER_EXECUTABLE");
  if (configured) {
    if (!existsExecutable(configured)) {
      throw new Error(`LOOM_BROWSER_EXECUTABLE does not exist or is not executable: ${configured}`);
    }
    return configured;
  }

  const candidates = candidateMap();
  for (const engine of preferredOrder()) {
    for (const candidate of candidates[engine] || []) {
      if (candidate && existsExecutable(candidate)) return candidate;
    }
  }
  return "";
}

function npmExecutable() {
  return process.platform === "win32" ? "npm.cmd" : "npm";
}

function browserBasename(executable) {
  return path.basename(executable).replace(/\.exe$/i, "");
}

function defaultProfileDir() {
  return path.join(REPO_ROOT, ".loom", "browser", "cdp-profile");
}

function defaultLogDir() {
  return path.join(REPO_ROOT, ".loom", "logs", "browser-use");
}

function ensureCdpEndpoint({ dryRun = false } = {}) {
  const configured = envText("LOOM_BROWSER_CDP_URL");
  if (configured) {
    log(`using existing LOOM_BROWSER_CDP_URL=${configured}`);
    return { cdpUrl: configured, launched: false, configured: true };
  }
  if (flagDisabled("LOOM_DESKTOP_BROWSER_CDP")) {
    throw new Error("Browser CDP bootstrap is disabled by LOOM_DESKTOP_BROWSER_CDP=0");
  }

  const browser = findBrowserExecutable();
  if (!browser) {
    throw new Error(
      "Could not find Microsoft Edge or Google Chrome. Set LOOM_BROWSER_EXECUTABLE to the browser executable path."
    );
  }

  const port = parsePort();
  const cdpUrl = `http://127.0.0.1:${port}`;
  const profileDir = path.resolve(envText("LOOM_BROWSER_CDP_PROFILE_DIR") || defaultProfileDir());
  const launchArgs = [
    "--remote-debugging-address=127.0.0.1",
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profileDir}`,
    "--no-first-run",
    "--no-default-browser-check",
    "--new-window",
    "about:blank",
  ];

  if (dryRun) {
    log(`would launch ${browserBasename(browser)} with CDP at ${cdpUrl}`);
    log(`profile: ${profileDir}`);
    return { cdpUrl, browser, profileDir, launched: false, dryRun: true };
  }

  fs.mkdirSync(profileDir, { recursive: true });
  const child = spawn(browser, launchArgs, {
    cwd: REPO_ROOT,
    detached: true,
    stdio: "ignore",
    windowsHide: false,
  });
  child.unref();
  process.env.LOOM_BROWSER_CDP_URL = cdpUrl;
  log(`launched ${browserBasename(browser)} with CDP at ${cdpUrl}`);
  log(`profile: ${profileDir}`);
  return { cdpUrl, browser, profileDir, launched: true };
}

function runNpmScript(scriptName, env) {
  const child = spawn(npmExecutable(), ["run", scriptName], {
    cwd: DESKTOP_ROOT,
    env,
    stdio: "inherit",
    shell: false,
  });
  child.on("exit", (code, signal) => {
    if (signal) {
      warn(`npm run ${scriptName} stopped by ${signal}`);
      process.exit(1);
    }
    process.exit(code ?? 0);
  });
  child.on("error", (error) => {
    console.error(error);
    process.exit(1);
  });
}

function usage() {
  console.log(`Usage:
  node scripts/start-browser-cdp.mjs --no-run
  node scripts/start-browser-cdp.mjs --dry-run
  node scripts/start-browser-cdp.mjs dev
  node scripts/start-browser-cdp.mjs start

Environment:
  LOOM_BROWSER_EXECUTABLE       Absolute browser executable path override
  LOOM_DESKTOP_BROWSER          edge | chrome | system, default edge
  LOOM_BROWSER_CDP_PORT         CDP port, default 9222
  LOOM_BROWSER_CDP_PROFILE_DIR  Dedicated CDP browser profile directory
  LOOM_BROWSER_CDP_URL          Existing local CDP endpoint to reuse
  LOOM_BROWSER_LOG_DIR          Browser diagnostic log folder, default <repo>/.loom/logs/browser-use
  LOOM_BROWSER_DIAGNOSTICS=0    Disable Browser Use diagnostics
  LOOM_DESKTOP_BROWSER_CDP=0    Disable this bootstrap helper
`);
}

const args = process.argv.slice(2);
const dryRun = args.includes("--dry-run");
const noRun = args.includes("--no-run") || dryRun;
const scriptName = args.find((item) => !item.startsWith("--"));
const logDir = envText("LOOM_BROWSER_LOG_DIR", defaultLogDir());

if (args.includes("--help") || args.includes("-h")) {
  usage();
  process.exit(0);
}

try {
  const info = ensureCdpEndpoint({ dryRun });
  const env = {
    ...process.env,
    LOOM_BROWSER_CDP_URL: info.cdpUrl,
    LOOM_BROWSER_LOG_DIR: logDir,
    LOOM_BROWSER_DIAGNOSTICS: flagDisabled("LOOM_BROWSER_DIAGNOSTICS") ? "0" : "1",
  };
  log(`diagnostics: ${logDir}`);
  if (noRun || !scriptName) {
    log(`LOOM_BROWSER_CDP_URL=${info.cdpUrl}`);
    log("start Loom from this helper so Electron and Python inherit the CDP endpoint.");
    process.exit(0);
  }
  runNpmScript(scriptName, env);
} catch (error) {
  console.error(`[browser-cdp] ${error instanceof Error ? error.message : String(error)}`);
  process.exit(1);
}
