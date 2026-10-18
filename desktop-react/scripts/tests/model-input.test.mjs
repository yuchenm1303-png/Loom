import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";
const source = readFileSync(new URL("../../src/components/modelInput.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { imageInputError, imageAttachmentState } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("legacy upstream wrappers and typed rejections become plain capability notices in both languages", () => {
  for (const raw of ["AITransportError: Error code: 400: Beam supports text input only. This conversation contains an image.",
    "MODEL_IMAGE_INPUT_UNSUPPORTED: This model does not support image input."]) {
    for (const zh of [false, true]) {
      const notice = imageInputError(raw, zh);
      assert.ok(notice);
      assert.doesNotMatch(notice, /AITransportError|400|MODEL_IMAGE_INPUT_UNSUPPORTED|messages\[/);
    }
  }
  for (const error of ["API key expired", "Image URL is invalid", "Image is too large", "HTTP 500"]) {
    assert.equal(imageInputError(error, true), null, "unrelated errors keep their own diagnosis");
  }
});

test("new unsupported images block sending while unknown input stays explicit and plain text remains usable", () => {
  assert.equal(imageAttachmentState(true, false, false, true).blocked, true);
  assert.ok(imageAttachmentState(true, true, null, true).message);
  assert.equal(imageAttachmentState(true, true, null, true).blocked, false);
  assert.equal(imageAttachmentState(true, true, true, true).message, null);
  assert.deepEqual(imageAttachmentState(false, false, false, true), { blocked: false, message: null });
});
