import electronUpdater from "electron-updater";
import type { DownloadUpdateOptions } from "electron-updater/out/AppUpdater.js";
import type { ResolvedUpdateFileInfo } from "electron-updater/out/types.js";
import type { Provider } from "electron-updater/out/providers/Provider.js";
import type { BlockMap } from "builder-util-runtime/out/blockMapApi.js";
import { computeOperations } from "electron-updater/out/differentialDownloader/downloadPlanBuilder.js";
import { CancellationError } from "builder-util-runtime";
import { readFile, writeFile, rm, appendFile } from "node:fs/promises";
import { gunzipSync, gzipSync } from "node:zlib";
import path from "node:path";
import { downloadUpdate } from "./updateDownload.js";

// Override only the differential transfer. electron-updater continues to own
// release discovery, cache promotion, signature verification, and installation.
export class OptimizedNsisUpdater extends electronUpdater.NsisUpdater {
  protected override async differentialDownloadInstaller(
    fileInfo: ResolvedUpdateFileInfo, options: DownloadUpdateOptions,
    destination: string, provider: Provider<any>, oldInstallerName: string,
  ): Promise<boolean> {
    const controller = new AbortController();
    const cancel = () => controller.abort(new CancellationError());
    options.cancellationToken.on("cancel", cancel);
    if (options.cancellationToken.cancelled) cancel();
    const pendingMap = path.join(this.downloadedUpdateHelper!.cacheDirForPendingUpdate, "current.blockmap");
    try {
      const headers = new Headers();
      for (const [name, value] of Object.entries(options.requestHeaders || {})) {
        if (value != null) headers.set(name, Array.isArray(value) ? value.join(", ") : String(value));
      }
      const urls = await provider.getBlockMapFiles(fileInfo.url, this.currentVersion.version,
        options.updateInfoAndProvider.info.version, this.previousBlockmapBaseUrlOverride);
      const loadMap = async (url: URL): Promise<BlockMap> => {
        const response = await this.netSession.fetch(url.href, {
          headers, signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20_000)]),
        });
        if (!response.ok) { await response.body?.cancel(); throw new Error(`Blockmap HTTP ${response.status}`); }
        const reader = response.body?.getReader();
        if (!reader) throw new Error("Missing update blockmap");
        const chunks: Uint8Array[] = [];
        let size = 0;
        try {
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            size += value.length;
            if (size > 8 * 1024 * 1024) throw new Error("Update blockmap too large");
            chunks.push(value);
          }
        } finally { await reader.cancel().catch(() => {}); }
        return JSON.parse(gunzipSync(Buffer.concat(chunks), { maxOutputLength: 32 * 1024 * 1024 }).toString());
      };
      const oldMap = (async () => {
        try {
          return JSON.parse(gunzipSync(await readFile(path.join(this.downloadedUpdateHelper!.cacheDir, "current.blockmap")),
            { maxOutputLength: 32 * 1024 * 1024 }).toString()) as BlockMap;
        } catch { return loadMap(urls[0]); }
      })();
      const [old, next] = await Promise.all([oldMap, loadMap(urls[1])]);
      if (old.version !== next.version) throw new Error("Update blockmap format changed");
      const operations = computeOperations(old, next, this._logger);
      let lastProgress = 0;
      let resolvedDownloadUrl: Promise<string> | null = null;
      const stats = await downloadUpdate({
        operations, size: fileInfo.info.size!, sha512: fileInfo.info.sha512,
        oldFile: path.join(this.downloadedUpdateHelper!.cacheDir, oldInstallerName),
        newFile: destination, signal: controller.signal,
        fetchRange: (range, signal) => {
          const rangeHeaders = new Headers(headers);
          rangeHeaders.set("Range", `bytes=${range.start}-${range.end - 1}`);
          if (!resolvedDownloadUrl) {
            const first = this.netSession.fetch(fileInfo.url.href, { headers: rangeHeaders, signal });
            // GitHub redirects to a signed asset URL. Reuse that resolved URL
            // for the remaining ranges instead of repeating the redirect chain.
            resolvedDownloadUrl = first.then(response => response.url || fileInfo.url.href);
            // A failed initial fetch is handled by downloadUpdate's workers.
            const resolving = resolvedDownloadUrl;
            void resolving.catch(() => {
              if (resolvedDownloadUrl === resolving) resolvedDownloadUrl = null;
            });
            return first;
          }
          return resolvedDownloadUrl.then(url => {
            if (new URL(url).origin !== fileInfo.url.origin) {
              // Match Fetch's cross-origin redirect handling for credentials.
              for (const name of ["authorization", "cookie", "proxy-authorization"]) rangeHeaders.delete(name);
            }
            return this.netSession.fetch(url, { headers: rangeHeaders, signal });
          });
        },
        onProgress: (transferred, total, elapsedMs) => {
          if (Date.now() - lastProgress < 250) return;
          lastProgress = Date.now();
          this.emit("download-progress", { total, transferred, delta: 0,
            percent: total ? Math.min(99.9, transferred * 100 / total) : 0,
            bytesPerSecond: Math.round(transferred * 1000 / Math.max(1, elapsedMs)) });
        },
      });
      controller.signal.throwIfAborted();
      await writeFile(pendingMap, gzipSync(JSON.stringify(next)));
      await this.recordDownload({ mode: "parallel-differential", version: options.updateInfoAndProvider.info.version, ...stats });
      return false;
    } catch (error) {
      if (options.cancellationToken.cancelled) throw new CancellationError();
      // No writes are still running when downloadUpdate rejects. The upstream
      // full downloader can safely truncate and replace the failed output.
      await rm(pendingMap, { force: true });
      this._logger.warn(`Optimized update failed; falling back to full download: ${error instanceof Error ? error.message : "transfer failed"}`);
      await this.recordDownload({ mode: "full-fallback", version: options.updateInfoAndProvider.info.version });
      return true;
    } finally {
      controller.abort();
      options.cancellationToken.removeListener("cancel", cancel);
    }
  }

  private async recordDownload(details: Record<string, unknown>) {
    const entry = { time: new Date().toISOString(), ...details };
    this._logger.info(JSON.stringify(entry));
    // Only metrics; never signed URLs, headers, credentials or account data.
    await appendFile(path.join(this.downloadedUpdateHelper!.cacheDir, "download-metrics.jsonl"), `${JSON.stringify(entry)}\n`).catch(() => {});
  }
}
