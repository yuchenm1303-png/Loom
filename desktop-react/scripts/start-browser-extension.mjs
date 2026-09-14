#!/usr/bin/env node
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DESKTOP_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(DESKTOP_ROOT, "..");
const DEFAULT_PORT = "39222";
const DEFAULT_TOKEN = "loom-dev-browser-extension";

function log(message) {
  console.log(`[browser-extension] ${message}`);
}

function envText(name, fallback = "") {
  return String(process.env[name] || fallback).trim();
}

function npmExecutable() {
  return process.platform === "win32" ? "npm.cmd" : "npm";
}

function ensureLogDir(logDir) {
  fs.mkdirSync(logDir, { recursive: true });
}

function usage() {
  console.log(`Usage:
  node scripts/start-browser-extension.mjs --no-run
  node scripts/start-browser-extension.mjs dev
  node scripts/start-browser-extension.mjs start

Environment:
  LOOM_BROWSER_EXTENSION_PORT   Local bridge port, default 39222
  LOOM_BROWSER_EXTENSION_TOKEN  Shared bridge token, default loom-dev-browser-extension
  LOOM_BROWSER_LOG_DIR          Browser diagnostic log folder, default <repo>/.loom/logs/browser-use
  LOOM_BROWSER_BACKEND          Set to extension by this helper
`);
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
      console.warn(`[browser-extension] npm run ${scriptName} stopped by ${signal}`);
      process.exit(1);
    }
    process.exit(code ?? 0);
  });
  child.on("error", (error) => {
    console.error(error);
    process.exit(1);
  });
}

const args = process.argv.slice(2);
if (args.includes("--help") || args.includes("-h")) {
  usage();
  process.exit(0);
}

const noRun = args.includes("--no-run");
const scriptName = args.find((item) => !item.startsWith("--"));
const port = envText("LOOM_BROWSER_EXTENSION_PORT", DEFAULT_PORT);
const token = envText("LOOM_BROWSER_EXTENSION_TOKEN", DEFAULT_TOKEN);
const logDir = envText("LOOM_BROWSER_LOG_DIR", path.join(REPO_ROOT, ".loom", "logs", "browser-use"));
const extensionDir = path.join(REPO_ROOT, "extensions", "browser-current-tab");
const bridgeUrl = `http://127.0.0.1:${port}`;

ensureLogDir(logDir);

const env = {
  ...process.env,
  LOOM_BROWSER_BACKEND: "extension",
  LOOM_BROWSER_EXTENSION: "1",
  LOOM_BROWSER_EXTENSION_PORT: port,
  LOOM_BROWSER_EXTENSION_TOKEN: token,
  LOOM_BROWSER_LOG_DIR: logDir,
  LOOM_BROWSER_DIAGNOSTICS: "1",
};

log(`backend: extension`);
log(`bridge: ${bridgeUrl}`);
log(`extension folder: ${extensionDir}`);
log(`diagnostics: ${logDir}`);
log("token: configured (value hidden)");
log("load the extension folder as an unpacked Chrome/Edge extension before asking Loom to inspect the current tab.");

if (noRun || !scriptName) {
  process.exit(0);
}

runNpmScript(scriptName, env);
