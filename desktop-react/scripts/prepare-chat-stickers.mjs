import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { inflateRawSync } from "node:zlib";

const SOURCE_COMMIT = "e04ffe3bee59a376eb61286fb3d0da2d2e3c7fe3";
const SOURCE_BLOB_SHA = "a316dba7246c03e280101b683f2ae9f1434b5fd9";
const SOURCE_URLS = [
  `https://raw.githubusercontent.com/yuchenm1303-png/yuchen1303.github.io/${SOURCE_COMMIT}/ai-ledger-android/app/src/main/assets/inline_stickers_v1.zip`,
  `https://github.com/yuchenm1303-png/yuchen1303.github.io/raw/${SOURCE_COMMIT}/ai-ledger-android/app/src/main/assets/inline_stickers_v1.zip`,
];

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
const desktopRoot = join(scriptDir, "..");
const outputDir = join(desktopRoot, "public", "chat-stickers");
const sourceCacheDir = join(desktopRoot, ".cache", "chat-stickers");
const sourceCachePath = join(sourceCacheDir, `inline_stickers_v1-${SOURCE_BLOB_SHA}.zip`);
const manifestPath = join(outputDir, ".source.json");

function gitBlobSha(buffer) {
  return createHash("sha1")
    .update(`blob ${buffer.length}\0`)
    .update(buffer)
    .digest("hex");
}

function isWebP(buffer) {
  return buffer.length > 12
    && buffer.subarray(0, 4).equals(Buffer.from("RIFF"))
    && buffer.subarray(8, 12).equals(Buffer.from("WEBP"));
}

function findEndOfCentralDirectory(buffer) {
  const signature = 0x06054b50;
  const minOffset = Math.max(0, buffer.length - 65_557);
  for (let offset = buffer.length - 22; offset >= minOffset; offset -= 1) {
    if (buffer.readUInt32LE(offset) === signature) return offset;
  }
  throw new Error("Sticker pack is not a supported ZIP archive: EOCD missing");
}

function unzipEntries(buffer) {
  const eocd = findEndOfCentralDirectory(buffer);
  const diskNumber = buffer.readUInt16LE(eocd + 4);
  const centralDisk = buffer.readUInt16LE(eocd + 6);
  const diskEntries = buffer.readUInt16LE(eocd + 8);
  const totalEntries = buffer.readUInt16LE(eocd + 10);
  const centralSize = buffer.readUInt32LE(eocd + 12);
  const centralOffset = buffer.readUInt32LE(eocd + 16);

  if (diskNumber !== 0 || centralDisk !== 0 || diskEntries !== totalEntries) {
    throw new Error("Sticker pack uses unsupported multi-disk ZIP layout");
  }
  if (totalEntries === 0xffff || centralSize === 0xffffffff || centralOffset === 0xffffffff) {
    throw new Error("Sticker pack unexpectedly requires ZIP64");
  }
  if (centralOffset + centralSize > buffer.length) {
    throw new Error("Sticker pack central directory is out of bounds");
  }

  const entries = new Map();
  let offset = centralOffset;
  for (let index = 0; index < totalEntries; index += 1) {
    if (offset + 46 > buffer.length || buffer.readUInt32LE(offset) !== 0x02014b50) {
      throw new Error(`Sticker pack central entry ${index} is invalid`);
    }

    const flags = buffer.readUInt16LE(offset + 8);
    const method = buffer.readUInt16LE(offset + 10);
    const compressedSize = buffer.readUInt32LE(offset + 20);
    const uncompressedSize = buffer.readUInt32LE(offset + 24);
    const fileNameLength = buffer.readUInt16LE(offset + 28);
    const extraLength = buffer.readUInt16LE(offset + 30);
    const commentLength = buffer.readUInt16LE(offset + 32);
    const localOffset = buffer.readUInt32LE(offset + 42);
    const nameStart = offset + 46;
    const nameEnd = nameStart + fileNameLength;
    if (nameEnd > buffer.length) throw new Error("Sticker pack entry name is out of bounds");

    const entryName = buffer.subarray(nameStart, nameEnd).toString("utf8");
    offset = nameEnd + extraLength + commentLength;
    if (entryName.endsWith("/")) continue;
    if (flags & 0x0001) throw new Error(`Encrypted sticker ZIP entry is not supported: ${entryName}`);
    if (compressedSize === 0xffffffff || uncompressedSize === 0xffffffff || localOffset === 0xffffffff) {
      throw new Error(`ZIP64 sticker entry is not supported: ${entryName}`);
    }
    if (localOffset + 30 > buffer.length || buffer.readUInt32LE(localOffset) !== 0x04034b50) {
      throw new Error(`Sticker ZIP local entry is invalid: ${entryName}`);
    }

    const localNameLength = buffer.readUInt16LE(localOffset + 26);
    const localExtraLength = buffer.readUInt16LE(localOffset + 28);
    const dataStart = localOffset + 30 + localNameLength + localExtraLength;
    const dataEnd = dataStart + compressedSize;
    if (dataEnd > buffer.length) throw new Error(`Sticker ZIP entry is truncated: ${entryName}`);

    const compressed = buffer.subarray(dataStart, dataEnd);
    let data;
    if (method === 0) data = Buffer.from(compressed);
    else if (method === 8) data = inflateRawSync(compressed);
    else throw new Error(`Unsupported ZIP compression method ${method}: ${entryName}`);

    if (data.length !== uncompressedSize) {
      throw new Error(`Sticker ZIP entry size mismatch: ${entryName}`);
    }
    entries.set(entryName, data);
  }
  return entries;
}

async function validatePreparedDirectory() {
  try {
    const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
    if (manifest.sourceBlobSha !== SOURCE_BLOB_SHA || manifest.count !== EXPECTED_KEYS.length) return false;
    for (const key of EXPECTED_KEYS) {
      const file = await readFile(join(outputDir, `${key}.webp`));
      if (!isWebP(file)) return false;
    }
    return true;
  } catch {
    return false;
  }
}

async function fetchSourceArchive() {
  if (existsSync(sourceCachePath)) {
    const cached = await readFile(sourceCachePath);
    if (gitBlobSha(cached) === SOURCE_BLOB_SHA) return cached;
    await rm(sourceCachePath, { force: true });
  }

  let lastError = null;
  for (const url of SOURCE_URLS) {
    try {
      const response = await fetch(url, {
        redirect: "follow",
        headers: { "user-agent": "Loom sticker asset preparation" },
      });
      if (!response.ok) throw new Error(`HTTP ${response.status} ${response.statusText}`);
      const archive = Buffer.from(await response.arrayBuffer());
      const actualSha = gitBlobSha(archive);
      if (actualSha !== SOURCE_BLOB_SHA) {
        throw new Error(`source Git blob mismatch: expected ${SOURCE_BLOB_SHA}, got ${actualSha}`);
      }
      await mkdir(sourceCacheDir, { recursive: true });
      const tempPath = `${sourceCachePath}.${process.pid}.tmp`;
      await writeFile(tempPath, archive);
      await rename(tempPath, sourceCachePath);
      return archive;
    } catch (error) {
      lastError = error;
    }
  }
  throw new Error(`Unable to obtain original AI Ledger sticker pack: ${lastError?.message || lastError}`);
}

async function prepare() {
  if (await validatePreparedDirectory()) {
    console.log(`Chat stickers ready: ${EXPECTED_KEYS.length} local WebP assets`);
    return;
  }

  const archive = await fetchSourceArchive();
  const entries = unzipEntries(archive);
  const byBaseName = new Map();
  for (const [entryName, data] of entries) {
    const fileName = basename(entryName);
    if (!fileName.toLowerCase().endsWith(".webp")) continue;
    if (byBaseName.has(fileName)) throw new Error(`Duplicate sticker asset in source pack: ${fileName}`);
    byBaseName.set(fileName, data);
  }

  if (byBaseName.size !== EXPECTED_KEYS.length) {
    throw new Error(`Expected ${EXPECTED_KEYS.length} WebP stickers, found ${byBaseName.size}`);
  }

  const stagingDir = `${outputDir}.staging-${process.pid}`;
  await rm(stagingDir, { recursive: true, force: true });
  await mkdir(stagingDir, { recursive: true });

  let totalBytes = 0;
  for (const key of EXPECTED_KEYS) {
    const fileName = `${key}.webp`;
    const data = byBaseName.get(fileName);
    if (!data) throw new Error(`Missing expected sticker asset: ${fileName}`);
    if (!isWebP(data)) throw new Error(`Invalid WebP sticker asset: ${fileName}`);
    if (data.length < 1024 || data.length > 512 * 1024) {
      throw new Error(`Unexpected sticker size for ${fileName}: ${data.length} bytes`);
    }
    totalBytes += data.length;
    await writeFile(join(stagingDir, fileName), data);
  }

  await writeFile(
    join(stagingDir, ".source.json"),
    `${JSON.stringify({
      schema: "loom_chat_stickers_v1",
      sourceRepository: "yuchenm1303-png/yuchen1303.github.io",
      sourceCommit: SOURCE_COMMIT,
      sourcePath: "ai-ledger-android/app/src/main/assets/inline_stickers_v1.zip",
      sourceBlobSha: SOURCE_BLOB_SHA,
      count: EXPECTED_KEYS.length,
    }, null, 2)}\n`,
    "utf8",
  );

  await rm(outputDir, { recursive: true, force: true });
  await mkdir(dirname(outputDir), { recursive: true });
  await rename(stagingDir, outputDir);
  console.log(`Prepared ${EXPECTED_KEYS.length} original local WebP stickers (${totalBytes} bytes)`);
}

await prepare();
