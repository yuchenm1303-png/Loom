// Playwright's Windows Python wheel ships an x64 Node driver even when the
// interpreter itself is win32-x86. Native x86 Loom must never execute it.
// This script replaces ONLY the isolated IA32 packaging virtualenv's driver,
// with a checksum-verified upstream Windows x86 Node.js executable.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const driver = path.join(project, ".packaging-venv-x86", "Lib", "site-packages", "playwright", "driver", "node.exe");
const nodeVersion = "22.23.4";
const source = `https://nodejs.org/dist/v${nodeVersion}/`;
const candidate = "win-x86/node.exe";

function machine(file) {
  const buffer = fs.readFileSync(file);
  if (buffer.length < 256 || buffer.subarray(0, 2).toString() !== "MZ") {
    throw new Error("Not a valid Windows PE binary: " + file);
  }
  const pos = buffer.readUInt32LE(0x3c);
  if (pos > buffer.length - 6 || buffer.toString("ascii", pos, pos + 4) !== "PE\\0\\0") {
    throw new Error("Invalid Windows PE signature: " + file);
  }
  return buffer.readUInt16LE(pos + 4);
}

if (process.platform !== "win32") throw new Error("x86 Playwright driver prep requires Windows");
if (!fs.existsSync(driver)) throw new Error("Playwright's bundled Windows driver is missing: " + driver);
const original = machine(driver);
console.log("[win32-local] bundled Playwright driver machine: 0x" + original.toString(16));
if (original !== 0x014c) {
  // Do not trust a binary just because the URL contains x86: fetch the official
  // SHASUMS256 list over HTTPS, verify the actual bytes, then verify the PE arch.
  const response = await fetch(source + "SHASUMS256.txt", { signal: AbortSignal.timeout(60000) });
  if (!response.ok) throw new Error("Cannot fetch official Node.js checksums: " + response.status);
  const checksums = await response.text();
  const row = checksums.split(/\r?\n/).find(line => line.trim().endsWith("  " + candidate));
  if (!row) throw new Error("Official Node.js checksum not published for " + candidate);
  const expected = row.trim().split(/\s+/)[0];
  if (!/^[0-9a-f]{64}$/i.test(expected)) throw new Error("Invalid official SHA256 digest");
  const binary = await fetch(source + candidate, { signal: AbortSignal.timeout(180000) });
  if (!binary.ok) throw new Error("Cannot fetch official Node.js x86 executable: " + binary.status);
  const bytes = Buffer.from(await binary.arrayBuffer());
  const actual = crypto.createHash("sha256").update(bytes).digest("hex");
  if (actual.toLowerCase() !== expected.toLowerCase()) throw new Error("Node.js x86 binary SHA256 mismatch");
  const staged = driver + ".x86-staged";
  fs.writeFileSync(staged, bytes, { flag: "w" });
  try {
    if (machine(staged) !== 0x014c) throw new Error("Downloaded Node.js is not native x86");
    const version = spawnSync(staged, ["--version"], { encoding: "utf8", timeout: 30000, windowsHide: true });
    if (version.status !== 0 || version.stdout.trim() !== "v" + nodeVersion) {
      throw new Error("Node.js x86 driver runtime verification failed: " + (version.stderr || version.error || ""));
    }
    fs.renameSync(staged, driver);
  } finally {
    if (fs.existsSync(staged)) fs.rmSync(staged, { force: true });
  }
}
if (machine(driver) !== 0x014c) throw new Error("Playwright node.exe remains incompatible with 32-bit Windows");
const check = spawnSync(driver, ["--version"], { encoding: "utf8", timeout: 30000, windowsHide: true });
if (check.status !== 0) throw new Error("Bundled x86 browser driver cannot run");
console.log("[win32-local] x86 Playwright driver ready:", check.stdout.trim());
