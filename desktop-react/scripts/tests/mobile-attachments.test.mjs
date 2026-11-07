import { test } from "node:test";
import assert from "node:assert/strict";
import { buildSync } from "esbuild";
import { fileURLToPath } from "node:url";

const bundle = buildSync({ entryPoints: [fileURLToPath(new URL("../../src/mobile/attachmentQueue.ts", import.meta.url))], bundle: true, write: false, format: "esm", platform: "node" });
const { AttachmentQueue, MAX_ATTACHMENT_BYTES, fileSize } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`);
const file = (name = "报告.pdf") => new File(["content"], name, { lastModified: 1 });
const tick = () => new Promise((resolve) => setImmediate(resolve));

test("uploads serially, retains original names, ignores duplicate selections", async () => {
  const pending = [];
  const queue = new AttachmentQueue((file, uploading) => { uploading(); return new Promise((resolve) => pending.push({ name: file.name, resolve })); });
  const first = file();
  queue.add([first, first, file("photo.jpg")]);
  assert.equal(pending.length, 1);
  assert.deepEqual(queue.snapshot().map((item) => item.status), ["uploading", "queued"]);
  pending[0].resolve("C:/tmp/random.pdf");
  await tick();
  assert.equal(pending.length, 2);
  assert.equal(queue.snapshot()[0].file.name, "报告.pdf");
  assert.equal(queue.snapshot()[0].path, "C:/tmp/random.pdf");
  pending[1].resolve("C:/tmp/photo.jpg");
  await tick();
  assert.ok(queue.snapshot().every((item) => item.status === "ready"));
  queue.dispose();
});

test("per-file failures keep other uploads and retry only the failed file", async () => {
  let fails = true;
  const calls = [];
  const queue = new AttachmentQueue(async (file) => {
    calls.push(file.name);
    if (file.name === "bad.pdf" && fails) throw Object.assign(new Error("offline"), { code: "HOST_OFFLINE" });
    return `C:/tmp/${file.name}`;
  });
  queue.add([file("bad.pdf"), file("good.txt")]);
  await tick();
  const failed = queue.snapshot()[0];
  assert.equal(failed.status, "error");
  assert.match(failed.error, /离线/);
  assert.equal(queue.snapshot()[1].status, "ready");
  fails = false;
  queue.retry(failed.id);
  await tick();
  assert.deepEqual(calls, ["bad.pdf", "good.txt", "bad.pdf"]);
  assert.ok(queue.snapshot().every((item) => item.status === "ready"));
  queue.dispose();
});

test("removing in-flight files and disposing a draft ignore late successes", async () => {
  let resolve;
  const queue = new AttachmentQueue(() => new Promise((done) => { resolve = done; }));
  queue.add([file()]);
  queue.remove(queue.snapshot()[0].id);
  resolve("C:/tmp/late.pdf");
  await tick();
  assert.deepEqual(queue.snapshot(), []);
  queue.add([file("next.txt")]);
  queue.dispose();
  resolve("C:/tmp/next.txt");
  await tick();
  assert.deepEqual(queue.snapshot(), []);
  assert.deepEqual(queue.add([file("ignored.txt")]), []);
});

test("size, total size and count limits reject files before reading or uploading", async () => {
  const calls = [];
  const queue = new AttachmentQueue(async (file) => { calls.push(file.name); return file.name; });
  const oversized = { name: "large.zip", size: MAX_ATTACHMENT_BYTES + 1, lastModified: 1 };
  assert.match(queue.add([oversized])[0], /10 MB/);
  const large = (name) => ({ name, size: MAX_ATTACHMENT_BYTES, lastModified: 1, type: "application/zip" });
  assert.equal(queue.add([large("a.zip"), large("b.zip")]).length, 0);
  queue.add([large("c.zip"), large("d.zip"), large("e.zip")]);
  assert.match(queue.add([file("extra.txt")])[0], /50 MB/);
  await tick();
  assert.deepEqual(calls, ["a.zip", "b.zip", "c.zip", "d.zip", "e.zip"]);
  queue.clear();
  assert.equal(queue.add(Array.from({ length: 11 }, (_, i) => file(`${i}.txt`))).length, 1);
  assert.equal(queue.snapshot().length, 10);
  queue.dispose();
});

test("previews release object URLs on removal and disposal", async () => {
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  const revoked = [];
  let count = 0;
  URL.createObjectURL = () => `blob:${++count}`;
  URL.revokeObjectURL = (url) => revoked.push(url);
  try {
    const queue = new AttachmentQueue(async () => "path");
    queue.add([new File(["a"], "a.png", { type: "image/png" }), new File(["b"], "b.png", { type: "image/png" })]);
    queue.remove(queue.snapshot()[0].id);
    queue.dispose();
    await tick();
    assert.deepEqual(revoked, ["blob:1", "blob:2"]);
    assert.equal(fileSize(0), "0 B");
    assert.equal(fileSize(MAX_ATTACHMENT_BYTES), "10.0 MB");
  } finally { URL.createObjectURL = originalCreate; URL.revokeObjectURL = originalRevoke; }
});
