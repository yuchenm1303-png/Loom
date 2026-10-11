import { test, mock } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, readFile, rm } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { gzipSync } from "node:zlib";
import { createHash } from "node:crypto";
import { CancellationToken, CancellationError } from "builder-util-runtime";

class FakeNsisUpdater {
  currentVersion = { version: "1.0.0" };
  _logger = { info() {}, warn() {}, error() {} };
  emit() {}
}
mock.module("electron-updater", { defaultExport: { NsisUpdater: FakeNsisUpdater } });
const { OptimizedNsisUpdater } = await import("../../dist-electron/optimizedUpdater.js");

for (const scenario of ["success", "redirect reuse", "range unsupported", "bad checksum", "cancelled"]) {
  test(`updater integration: ${scenario}`, async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "loom-optimized-updater-test-"));
    try {
      const pending = path.join(root, "pending");
      await mkdir(pending);
      const redirect = scenario === "redirect reuse";
      const old = redirect ? Buffer.alloc(2 * 1024 * 1024, 3) : Buffer.from("abcdefgh");
      const next = redirect ? Buffer.from(old) : Buffer.from("1234efgh");
      if (redirect) { next.fill(7, 0, 65536); next.fill(8, 1024 * 1024, 1024 * 1024 + 65536); }
      await writeFile(path.join(root, "installer.exe"), old);
      const map = changed => ({ version: "2", files: [{ name: "file", offset: 0,
        checksums: redirect ? [changed ? "new-a" : "old-a", "copy-a", changed ? "new-b" : "old-b", "copy-b"] : [changed ? "new" : "old", "same"],
        sizes: redirect ? [65536, 1024 * 1024 - 65536, 65536, 1024 * 1024 - 65536] : [4, 4] }] });
      await writeFile(path.join(root, "current.blockmap"), gzipSync(JSON.stringify(map(false))));
      const updater = new OptimizedNsisUpdater();
      updater.downloadedUpdateHelper = { cacheDir: root, cacheDirForPendingUpdate: pending };
      const cancellationToken = new CancellationToken();
      const requests = [];
      updater.netSession = { fetch: async (url, init) => {
        if (url.endsWith(".blockmap")) return new Response(gzipSync(JSON.stringify(map(true))));
        if (scenario === "cancelled") { cancellationToken.cancel(); throw new CancellationError(); }
        if (scenario === "range unsupported") return new Response(next);
        requests.push({ url, auth: init.headers.get("authorization") });
        const [, start, end] = /bytes=(\d+)-(\d+)/.exec(init.headers.get("range"));
        const response = new Response(next.subarray(Number(start), Number(end) + 1), {
          status: 206, headers: { "Content-Range": `bytes ${start}-${end}/${next.length}` },
        });
        if (redirect) Object.defineProperty(response, "url", { value: "https://assets.example.test/signed-installer.exe" });
        return response;
      } };
      const provider = { getBlockMapFiles: () => [new URL("https://example.test/old.blockmap"), new URL("https://example.test/new.blockmap")] };
      const info = { url: new URL("https://example.test/installer.exe"), info: { size: next.length,
        sha512: createHash("sha512").update(scenario === "bad checksum" ? old : next).digest("base64") } };
      const options = { requestHeaders: { Authorization: "test-secret" }, cancellationToken, updateInfoAndProvider: { provider, info: { version: "1.0.1" } } };
      const result = updater.differentialDownloadInstaller(info, options, path.join(root, "output.exe"), provider, "installer.exe");
      if (scenario === "cancelled") await assert.rejects(result, CancellationError);
      else {
        assert.equal(await result, scenario !== "success" && !redirect);
        if (scenario === "success" || redirect) {
          assert.deepEqual(await readFile(path.join(root, "output.exe")), next);
          assert.ok((await readFile(path.join(pending, "current.blockmap"))).length);
          if (redirect) {
            assert.equal(requests.length, 2);
            assert.equal(requests[0].url, "https://example.test/installer.exe");
            assert.equal(requests[1].url, "https://assets.example.test/signed-installer.exe");
            assert.equal(requests[0].auth, "test-secret");
            assert.equal(requests[1].auth, null);
          }
        } else await assert.rejects(readFile(path.join(pending, "current.blockmap")), { code: "ENOENT" });
      }
    } finally { await rm(root, { recursive: true, force: true }); }
  });
}
