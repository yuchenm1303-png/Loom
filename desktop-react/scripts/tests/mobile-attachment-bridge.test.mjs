import { test } from "node:test";
import assert from "node:assert/strict";
import { buildSync } from "esbuild";
import { fileURLToPath } from "node:url";

const bundle = buildSync({ entryPoints: [fileURLToPath(new URL("../../src/mobile/mobileBridge.ts", import.meta.url))], bundle: true, write: false, format: "esm", platform: "node" });
const { installMobileBridge, pickMobileFiles, uploadMobileFile } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`);
const state = { socket: "open", online: true };
let invoke;
let openAttachment;
globalThis.document = { documentElement: { dataset: {} } };
globalThis.window = {
  setTimeout, clearTimeout,
  LoomShell: {
    state: () => JSON.stringify(state),
    invoke: (...args) => invoke(...args),
    openAttachment: (...args) => openAttachment(...args),
  },
};
installMobileBridge();
const answer = (id, result, error) => window.__loomShellEvent(JSON.stringify({ type: "invoke_result", id, result, error }));

test("transfers the exact original bytes and name, including empty files", async () => {
  for (const bytes of [new Uint8Array([0, 128, 255, 65]), new Uint8Array()]) {
    let uploading = false;
    invoke = (id, operation, argsJson) => {
      const [name, encoded] = JSON.parse(argsJson);
      assert.equal(uploading, true);
      assert.equal(operation, "stageTempFile");
      assert.equal(name, "中文报告.bin");
      assert.deepEqual(Buffer.from(encoded, "base64"), Buffer.from(bytes));
      answer(id, "C:/tmp/staged.bin");
    };
    assert.equal(await uploadMobileFile(new File([bytes], "中文报告.bin"), () => { uploading = true; }), "C:/tmp/staged.bin");
  }
});

test("oversized files are rejected before reading bytes or using the socket", async () => {
  invoke = () => assert.fail("must not send an oversized file");
  await assert.rejects(uploadMobileFile({ size: 10 * 1024 * 1024 + 1, arrayBuffer: () => assert.fail("must not read") }), /10 MB/);
});

test("opens downloaded workspace attachments through Android and surfaces native errors", async () => {
  invoke = (id, operation, argsJson) => {
    assert.equal(operation, "readLocalArtifact");
    assert.deepEqual(JSON.parse(argsJson), [".loom/attachments/a/报告.pdf", "C:/project"]);
    answer(id, { base64: "YQ==", mimeType: "application/pdf" });
  };
  openAttachment = (id, name, mime, bytes) => {
    assert.deepEqual([name, mime, bytes], ["报告.pdf", "application/pdf", "YQ=="]);
    answer(id, true);
  };
  assert.equal(await window.loom.openLocalArtifact(".loom/attachments/a/报告.pdf", "C:/project"), true);
  openAttachment = (id) => answer(id, undefined, { message: "手机上没有可打开此类文件的应用，请安装后重试" });
  await assert.rejects(window.loom.openLocalArtifact(".loom/attachments/a/报告.pdf", "C:/project"), /没有可打开/);
});

test("picker cancellation and selection both remove their temporary input", async () => {
  for (const selected of [[], [new File(["a"], "a.txt")]]) {
    const events = new Map();
    let removed = false;
    let attached = false;
    const input = {
      files: selected,
      addEventListener: (name, listener) => events.set(name, listener),
      remove: () => { removed = true; },
      click: () => { assert.equal(attached, true); events.get(selected.length ? "change" : "cancel")(); },
    };
    document.createElement = () => input;
    document.body = { append: () => { attached = true; } };
    assert.deepEqual(await pickMobileFiles(), selected);
    assert.equal(removed, true);
    assert.equal(input.multiple, true);
  }
});

test("connection loss rejects an in-flight upload promptly", async () => {
  let sent;
  invoke = () => { sent(); };
  const started = new Promise((resolve) => { sent = resolve; });
  const upload = uploadMobileFile(new File(["a"], "a.txt"));
  await started;
  window.__loomShellEvent(JSON.stringify({ type: "state", state: { socket: "closed", online: false } }));
  await assert.rejects(upload, { code: "HOST_OFFLINE" });
});
