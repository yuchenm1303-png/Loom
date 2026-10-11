import { open } from "node:fs/promises";
import { createReadStream } from "node:fs";
import { createHash } from "node:crypto";

export interface UpdateOperation { kind: number; start: number; end: number }
export interface UpdateRange { start: number; end: number }
const MAX_RANGE = 4 * 1024 * 1024;
const MAX_GAP = 256 * 1024;

export function planUpdateRanges(operations: UpdateOperation[], size: number): UpdateRange[] {
  const ranges: UpdateRange[] = [];
  let offset = 0, downloadBytes = 0;
  for (const operation of operations) {
    const length = operation.end - operation.start;
    if (![0, 1].includes(operation.kind) || !Number.isSafeInteger(operation.start)
      || !Number.isSafeInteger(operation.end) || operation.start < 0 || length <= 0) throw new Error("Invalid update operation");
    if (operation.kind === 1) {
      if (operation.start !== offset) throw new Error("Invalid update download offset");
      ranges.push({ start: offset, end: offset + length });
      downloadBytes += length;
    }
    offset += length;
  }
  if (offset !== size || !Number.isSafeInteger(size)) throw new Error("Update size mismatch");
  let overhead = Math.max(2 * 1024 * 1024, Math.floor(downloadBytes / 2));
  const merged: UpdateRange[] = [];
  for (const range of ranges) {
    const previous = merged.at(-1);
    const gap = previous ? range.start - previous.end : Infinity;
    if (previous && gap <= MAX_GAP && gap <= overhead && range.end - previous.start <= MAX_RANGE) {
      previous.end = range.end;
      overhead -= gap;
    } else merged.push({ ...range });
  }
  return merged.flatMap(range => {
    const chunks: UpdateRange[] = [];
    for (let start = range.start; start < range.end; start += MAX_RANGE) chunks.push({ start, end: Math.min(start + MAX_RANGE, range.end) });
    return chunks;
  });
}

export async function downloadUpdate(options: {
  operations: UpdateOperation[]; size: number; sha512: string; oldFile: string; newFile: string;
  signal: AbortSignal;
  fetchRange(range: UpdateRange, signal: AbortSignal): Promise<Response>;
  onProgress?(transferred: number, total: number, elapsedMs: number): void;
}) {
  const ranges = planUpdateRanges(options.operations, options.size);
  const total = ranges.reduce((sum, range) => sum + range.end - range.start, 0);
  const controller = new AbortController();
  const abort = () => controller.abort(options.signal.reason);
  options.signal.addEventListener("abort", abort, { once: true });
  if (options.signal.aborted) abort();
  const started = Date.now();
  let oldFile, newFile;
  try {
    controller.signal.throwIfAborted();
    oldFile = await open(options.oldFile, "r");
    newFile = await open(options.newFile, "w");
    await newFile.truncate(options.size);
    const buffer = Buffer.alloc(1024 * 1024);
    let offset = 0;
    for (const operation of options.operations) {
      if (operation.kind === 0) {
        for (let position = operation.start; position < operation.end;) {
          controller.signal.throwIfAborted();
          const length = Math.min(buffer.length, operation.end - position);
          const { bytesRead } = await oldFile.read(buffer, 0, length, position);
          if (bytesRead !== length) throw new Error("Cached installer is truncated");
          await writeAll(newFile, buffer.subarray(0, length), offset + position - operation.start);
          position += length;
        }
      }
      offset += operation.end - operation.start;
    }
    let next = 0, completed = 0;
    const networkStarted = Date.now();
    const activeBytes = new Map<number, number>();
    const progress = () => options.onProgress?.(completed + [...activeBytes.values()].reduce((a, b) => a + b, 0), total, Date.now() - networkStarted);
    const worker = async () => {
      while (next < ranges.length) {
        controller.signal.throwIfAborted();
        const index = next++, range = ranges[index], expected = range.end - range.start;
        for (let attempt = 0;; attempt++) {
          try {
            const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(20_000)]);
            const response = await options.fetchRange(range, signal);
            if (response.status !== 206 || response.headers.get("content-range") !== `bytes ${range.start}-${range.end - 1}/${options.size}`) {
              await response.body?.cancel();
              throw new Error("Update server returned an invalid byte range");
            }
            const reader = response.body?.getReader();
            if (!reader) throw new Error("Update server returned an empty body");
            const data = Buffer.allocUnsafe(expected);
            let received = 0;
            try {
              for (;;) {
                signal.throwIfAborted();
                const { done, value } = await reader.read();
                if (done) break;
                if (received + value.length > expected) throw new Error("Update range exceeded requested size");
                data.set(value, received);
                received += value.length;
                activeBytes.set(index, received);
                progress();
              }
            } finally { await reader.cancel().catch(() => {}); }
            if (received !== expected) throw new Error("Update range was truncated");
            await writeAll(newFile!, data, range.start);
            activeBytes.delete(index);
            completed += expected;
            progress();
            break;
          } catch (error) {
            activeBytes.delete(index);
            if (controller.signal.aborted || attempt >= 1) throw error;
          }
        }
      }
    };
    const workers = Array.from({ length: Math.min(4, ranges.length) }, () => worker().catch(error => {
      controller.abort(error);
      throw error;
    }));
    const results = await Promise.allSettled(workers);
    const failure = results.find(result => result.status === "rejected");
    if (failure?.status === "rejected") throw failure.reason;
    controller.signal.throwIfAborted();
    await newFile.close();
    newFile = undefined;
    const hash = createHash("sha512");
    for await (const chunk of createReadStream(options.newFile, { signal: controller.signal })) hash.update(chunk);
    const expectedHash = Buffer.from(options.sha512, /^[a-f\d]{128}$/i.test(options.sha512) ? "hex" : "base64");
    if (!hash.digest().equals(expectedHash)) throw new Error("Reconstructed installer SHA512 mismatch");
    return { ranges: ranges.length, bytes: total, elapsedMs: Date.now() - started };
  } finally {
    controller.abort();
    options.signal.removeEventListener("abort", abort);
    await Promise.all([oldFile?.close(), newFile?.close()]);
  }
}

async function writeAll(file: Awaited<ReturnType<typeof open>>, data: Buffer, position: number) {
  let written = 0;
  while (written < data.length) {
    const result = await file.write(data, written, data.length - written, position + written);
    if (!result.bytesWritten) throw new Error("Cannot write reconstructed installer");
    written += result.bytesWritten;
  }
}
