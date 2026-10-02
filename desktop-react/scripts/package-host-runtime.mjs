import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DESKTOP_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(DESKTOP_ROOT, "..");
const RUNTIME_SOURCE = path.join(DESKTOP_ROOT, "runtime-dist", "python");
const MXC_SOURCE = path.join(DESKTOP_ROOT, "node_modules", "@microsoft", "mxc-sdk", "bin", "x64", "wxc-exec.exe");
const EXTENSION_SOURCE = path.join(REPO_ROOT, "extensions", "browser-current-tab");
const OUTPUT_ROOT = path.join(DESKTOP_ROOT, "host-runtime-release");
const CONFIG_PATH = path.join(REPO_ROOT, "host-runtime", "runtime.json");

function argument(name, fallback = "") {
  const prefix = `--${name}=`;
  const found = process.argv.find((value) => value.startsWith(prefix));
  return found ? found.slice(prefix.length) : fallback;
}

function fail(message) {
  console.error(`[host-runtime] ${message}`);
  process.exit(2);
}

if (process.platform !== "win32") fail("Host runtime packaging must run on Windows.");
if (!fs.existsSync(RUNTIME_SOURCE)) fail(`Missing frozen runtime: ${RUNTIME_SOURCE}`);
if (!fs.existsSync(MXC_SOURCE)) fail(`Missing MXC executable: ${MXC_SOURCE}`);
if (!fs.existsSync(path.join(EXTENSION_SOURCE, "manifest.json"))) fail(`Missing browser extension: ${EXTENSION_SOURCE}`);

const config = JSON.parse(fs.readFileSync(CONFIG_PATH, "utf8"));
const version = String(argument("version", process.env.LOOM_HOST_RUNTIME_VERSION || "")).trim();
const protocol = Number(argument("protocol", process.env.LOOM_HOST_RUNTIME_PROTOCOL || config.protocol));
const minBootstrapVersion = String(argument("min-bootstrap", process.env.LOOM_HOST_MIN_BOOTSTRAP_VERSION || config.minBootstrapVersion || "")).trim();
const sourceSha = String(argument("source-sha", process.env.GITHUB_SHA || "")).trim();
if (!/^\d+\.\d+\.\d+$/.test(version)) fail(`Invalid runtime version: ${version}`);
if (!Number.isInteger(protocol) || protocol < 0) fail(`Invalid runtime protocol: ${protocol}`);
if (!/^\d+\.\d+\.\d+$/.test(minBootstrapVersion)) fail(`Invalid minimum bootstrap version: ${minBootstrapVersion}`);

fs.rmSync(OUTPUT_ROOT, { recursive: true, force: true });
fs.mkdirSync(OUTPUT_ROOT, { recursive: true });
const stage = path.join(OUTPUT_ROOT, `Loom-Host-Runtime-${version}-win-x64`);
fs.mkdirSync(stage, { recursive: true });
fs.cpSync(RUNTIME_SOURCE, stage, { recursive: true, force: true });
fs.copyFileSync(MXC_SOURCE, path.join(stage, "wxc-exec.exe"));
fs.cpSync(EXTENSION_SOURCE, path.join(stage, "browser-current-tab"), { recursive: true, force: true });

const manifest = {
  schema: 1,
  version,
  protocol,
  platform: "win32",
  arch: "x64",
  minBootstrapVersion,
  sourceSha: sourceSha || undefined,
  publishedAt: new Date().toISOString(),
};
fs.writeFileSync(path.join(stage, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`, "utf8");

const runtimeExe = path.join(stage, "python.exe");
const selfTest = spawnSync(runtimeExe, ["self-test"], { cwd: stage, encoding: "utf8", windowsHide: true, timeout: 120_000 });
if (selfTest.error) throw selfTest.error;
if (selfTest.status !== 0) fail(String(selfTest.stderr || selfTest.stdout || "Host runtime self-test failed."));

const archive = path.join(OUTPUT_ROOT, `Loom-Host-Runtime-${version}-win-x64.zip`);
const script = "& { param($src,$dst) Compress-Archive -Path (Join-Path $src '*') -DestinationPath $dst -CompressionLevel Optimal -Force }";
const zipped = spawnSync("powershell.exe", ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script, stage, archive], {
  encoding: "utf8",
  windowsHide: true,
  timeout: 300_000,
});
if (zipped.error) throw zipped.error;
if (zipped.status !== 0) fail(String(zipped.stderr || zipped.stdout || "Could not create Host runtime archive."));
if (!fs.existsSync(archive) || fs.statSync(archive).size < 1_000_000) fail("Host runtime archive is unexpectedly small.");

fs.copyFileSync(path.join(stage, "manifest.json"), path.join(OUTPUT_ROOT, "host-runtime-manifest.json"));
console.log(`[host-runtime] ${archive}`);
console.log(`[host-runtime] ${fs.statSync(archive).size} bytes`);
