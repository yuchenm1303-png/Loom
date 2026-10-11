import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import { mkdtemp, writeFile, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { downloadUpdate, planUpdateRanges } from "../../dist-electron/updateDownload.js";

const hash = data => createHash("sha512").update(data).digest("base64");
const response = (range, data) => new Response(data.subarray(range.start, range.end), {
  status: 206, headers: { "Content-Range": `bytes ${range.start}-${range.end - 1}/${data.length}` },
});

async function fixture(run) {
  const root = await mkdtemp(path.join(os.tmpdir(), "loom-update-transfer-"));
  try {
    const old = randomBytes(6 * 1024 * 1024), next = Buffer.from(old);
    const operations = [];
    for (let start = 0; start < old.length; start += 1024 * 1024) {
      next.fill(13, start, start + 65536);
      operations.push({ kind: 1, start, end: start + 65536 }, { kind: 0, start: start + 65536, end: start + 1024 * 1024 });
    }
    await writeFile(path.join(root, "old.exe"), old);
    await run({ operations, size: next.length, sha512: hash(next), oldFile: path.join(root, "old.exe"),
      newFile: path.join(root, "new.exe"), signal: new AbortController().signal }, next);
  } finally { await rm(root, { recursive: true, force: true }); }
}

test("adjacent fragments are merged with bounded overhead and request size", () => {
  const operations = [];
  for (let start = 0; start < 8 * 1024 * 1024; start += 32768) {
    operations.push({ kind: 1, start, end: start + 16384 }, { kind: 0, start: start + 16384, end: start + 32768 });
  }
  const ranges = planUpdateRanges(operations, 8 * 1024 * 1024);
  assert.ok(ranges.length < 256);
  assert.ok(ranges.every(range => range.end - range.start <= 4 * 1024 * 1024));
  assert.ok(ranges.reduce((sum, range) => sum + range.end - range.start, 0) <= 6 * 1024 * 1024);
});

test("parallel reconstruction respects four-worker limit and verifies exact file", async () => {
  await fixture(async (options, next) => {
    let active = 0, maximum = 0;
    const stats = await downloadUpdate({ ...options, fetchRange: async range => {
      active++; maximum = Math.max(maximum, active);
      await new Promise(resolve => setTimeout(resolve, 10));
      active--;
      return response(range, next);
    } });
    assert.equal(maximum, 4);
    assert.equal(stats.ranges, 6);
    assert.deepEqual(await readFile(options.newFile), next);
  });
});

test("merged ranges overwrite copied gaps and relocated old blocks correctly", async () => {
  await fixture(async (options, next) => {
    const old = await readFile(options.oldFile);
    const relocated = Buffer.concat([next.subarray(0, 65536), old.subarray(300000, 310000), next.subarray(75536, 85536)]);
    const operations = [{ kind: 1, start: 0, end: 65536 }, { kind: 0, start: 300000, end: 310000 }, { kind: 1, start: 75536, end: 85536 }];
    const stats = await downloadUpdate({ ...options, operations, size: relocated.length, sha512: hash(relocated), fetchRange: async range => response(range, relocated) });
    assert.equal(stats.ranges, 1);
    assert.deepEqual(await readFile(options.newFile), relocated);
  });
});

test("transient truncated response is retried once", async () => {
  await fixture(async (options, next) => {
    const attempts = new Map();
    await downloadUpdate({ ...options, fetchRange: async range => {
      const count = (attempts.get(range.start) || 0) + 1;
      attempts.set(range.start, count);
      return response(count === 1 ? { ...range, end: range.end - 1 } : range, next);
    } });
    assert.ok([...attempts.values()].every(count => count === 2));
  });
});

for (const problem of ["no range support", "wrong range", "truncated", "oversized", "corrupt"]) {
  test(`rejects ${problem} rather than accepting an invalid installer`, async () => {
    await fixture(async (options, next) => {
      await assert.rejects(downloadUpdate({ ...options, fetchRange: async range => {
        if (problem === "no range support") return new Response("ignored range", { status: 200 });
        const headers = { "Content-Range": problem === "wrong range" ? `bytes 0-1/${next.length}` : `bytes ${range.start}-${range.end - 1}/${next.length}` };
        let data = next.subarray(range.start, range.end);
        if (problem === "truncated") data = data.subarray(1);
        if (problem === "oversized") data = Buffer.concat([data, Buffer.from([0])]);
        if (problem === "corrupt") data = Buffer.alloc(data.length);
        return new Response(data, { status: 206, headers });
      } }));
    });
  });
}

test("cancellation drains concurrent workers before closing the output file", async () => {
  await fixture(async (options, next) => {
    const controller = new AbortController();
    let active = 0;
    await assert.rejects(downloadUpdate({ ...options, signal: controller.signal, fetchRange: async (range, signal) => {
      active++;
      try {
        if (active === 4) controller.abort();
        await new Promise(resolve => setTimeout(resolve, 5));
        signal.throwIfAborted();
        return response(range, next);
      } finally { active--; }
    } }));
    assert.equal(active, 0);
  });
});

test("invalid plans are rejected before writing", () => {
  assert.throws(() => planUpdateRanges([{ kind: 1, start: 1, end: 20 }], 19));
  assert.throws(() => planUpdateRanges([{ kind: 0, start: 0, end: 20 }], 19));
});
