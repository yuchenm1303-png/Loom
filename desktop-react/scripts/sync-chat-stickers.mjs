import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { inflateRawSync } from "node:zlib";

const SOURCE_COMMIT = "e04ffe3bee59a376eb61286fb3d0da2d2e3c7fe3";
const SOURCE_BLOB_SHA = "a316dba7246c03e280101b683f2ae9f1434b5fd9";
const SOURCE_URL = `https://raw.githubusercontent.com/yuchenm1303-png/yuchen1303.github.io/${SOURCE_COMMIT}/ai-ledger-android/app/src/main/assets/inline_stickers_v1.zip`;

const EXPECTED_KEYS = Object.freeze([
  "joy_burst",
  "affection_hug",
  "health_check",
  "thinking_soft",
  "cheer_power",
  "pout_no",
  "comfort_friend",
  "red_packet_congrats",
  "gift_for_you",
  "sparkle_excited",
  "soft_smile",
  "got_it_point",
  "heart_thanks",
  "confident_ready",
  "playful_wink",
  "confused_study",
  "confirm_yes",
  "idea_drawing",
  "reject_no",
]);

const scriptDir = dirname(fileURLToPath(import.meta.url));
const assetDir = join(scriptDir, "..", "src", "assets", "chat-stickers");

function gitBlobSha(buffer) {
  const header = Buffer.from(`blob ${buffer.length}\0`, "utf8");
  return createHash("sha1").update(header).update(buffer).digest("hex");
}

function isWebP(buffer) {
  return buffer.length >= 12
    && buffer.subarray(0, 4).toString("ascii") === "RIFF"
    && buffer.subarray(8, 12).toString("ascii") === "WEBP";
}

async function localAssetsAreReady() {
  try {
    for (const key of EXPECTED_KEYS) {
      const data = await readFile(join(assetDir, `${key}.webp`));
      if (!isWebP(data) || data.length < 1024) return false;
    }
    return true;
  } catch {
    return false;
  }
}

function findEndOfCentralDirectory(zip) {
  const minimumOffset = Math.max(0, zip.length - 65_557);
  for (let offset = zip.length - 22; offset >= minimumOffset; offset -= 1) {
    if (zip.readUInt32LE(offset) === 0x06054b50) return offset;
  }
  throw new Error("Invalid sticker ZIP: end-of-central-directory record not found");
}

function extractZipEntries(zip) {
  const eocd = findEndOfCentralDirectory(zip);
  const entryCount = zip.readUInt16LE(eocd + 10);
  const centralOffset = zip.readUInt32LE(eocd + 16);
  const entries = new Map();
  let offset = centralOffset;

  for (let index = 0; index < entryCount; index += 1) {
    if (zip.readUInt32LE(offset) !== 0x02014b50) {
      throw new Error(`Invalid sticker ZIP: central directory entry ${index} is corrupt`);
    }

    const flags = zip.readUInt16LE(offset + 8);
    const compression = zip.readUInt16LE(offset + 10);
    const compressedSize = zip.readUInt32LE(offset + 20);
    const uncompressedSize = zip.readUInt32LE(offset + 24);
    const nameLength = zip.readUInt16LE(offset + 28);
    const extraLength = zip.readUInt16LE(offset + 30);
    const commentLength = zip.readUInt16LE(offset + 32);
    const localOffset = zip.readUInt32LE(offset + 42);
    const name = zip.subarray(offset + 46, offset + 46 + nameLength).toString(flags & 0x0800 ? "utf8" : "latin1");

    if (!name.endsWith("/")) {
      if (zip.readUInt32LE(localOffset) !== 0x04034b50) {
        throw new Error(`Invalid sticker ZIP: local header is missing for ${name}`);
      }
      const localNameLength = zip.readUInt16LE(localOffset + 26);
      const localExtraLength = zip.readUInt16LE(localOffset + 28);
      const dataOffset = localOffset + 30 + localNameLength + localExtraLength;
      const compressed = zip.subarray(dataOffset, dataOffset + compressedSize);
      let data;
      if (compression === 0) {
        data = Buffer.from(compressed);
      } else if (compression === 8) {
        data = inflateRawSync(compressed);
      } else {
        throw new Error(`Unsupported ZIP compression method ${compression} for ${name}`);
      }
      if (data.length !== uncompressedSize) {
        throw new Error(`Invalid sticker ZIP: size mismatch for ${name}`);
      }
      entries.set(name.split("/").at(-1), data);
    }

    offset += 46 + nameLength + extraLength + commentLength;
  }

  return entries;
}

async function downloadSourceZip() {
  const response = await fetch(SOURCE_URL, { redirect: "follow" });
  if (!response.ok) {
    throw new Error(`Sticker source download failed: HTTP ${response.status}`);
  }
  const zip = Buffer.from(await response.arrayBuffer());
  const actualSha = gitBlobSha(zip);
  if (actualSha !== SOURCE_BLOB_SHA) {
    throw new Error(`Sticker source integrity check failed: expected ${SOURCE_BLOB_SHA}, got ${actualSha}`);
  }
  return zip;
}

async function main() {
  if (await localAssetsAreReady()) {
    console.log("Chat sticker assets already bundled locally.");
    return;
  }

  console.log("Restoring the original AI Ledger sticker pack for local bundling...");
  const zip = await downloadSourceZip();
  const entries = extractZipEntries(zip);
  await mkdir(assetDir, { recursive: true });

  for (const key of EXPECTED_KEYS) {
    const filename = `${key}.webp`;
    const data = entries.get(filename);
    if (!data) throw new Error(`Sticker source is missing ${filename}`);
    if (!isWebP(data)) throw new Error(`Sticker source contains invalid WebP: ${filename}`);
    await writeFile(join(assetDir, filename), data);
  }

  if (!(await localAssetsAreReady())) {
    throw new Error("Sticker asset verification failed after extraction");
  }
  console.log(`Bundled ${EXPECTED_KEYS.length} original WebP stickers into ${assetDir}.`);
}

main().catch((error) => {
  console.error(`\n[chat-stickers] ${error instanceof Error ? error.message : String(error)}`);
  console.error("A fresh checkout needs one network sync at build/dev time; the packaged Loom app uses only the local WebP assets.");
  process.exitCode = 1;
});
