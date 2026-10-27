import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { mock, test } from "node:test";

const userData = fs.mkdtempSync(path.join(os.tmpdir(), "loom-dialog-locations-"));
const written = [];
class FakeClipboardItem { constructor(items) { this.items = items; } }
mock.module("electron", { namedExports: {
  app: { getPath: () => userData },
  clipboard: { write: async (items) => { written.push(...items); } },
  ClipboardItem: FakeClipboardItem,
} });

const { writeClipboardImage } = await import("../../dist-electron/clipboardImage.js");

test("an image reaches the clipboard as PNG through ClipboardItem (clipboard.writeImage is gone in Electron 44)", async () => {
  const png = Buffer.from([0x89, 0x50, 0x4e, 0x47, 1, 2, 3]);
  await writeClipboardImage({ toPNG: () => png });
  assert.equal(written.length, 1);
  assert.deepEqual(Object.keys(written[0].items), ["image/png"]);
  const blob = written[0].items["image/png"];
  assert.equal(blob.type, "image/png");
  assert.deepEqual(Buffer.from(await blob.arrayBuffer()), png);
});

test("file pickers start where the last choice was, not in Downloads, and remember it across restarts", async () => {
  const projects = fs.mkdtempSync(path.join(userData, "projects-"));
  const chosen = path.join(projects, "loom-site");
  fs.mkdirSync(chosen);
  const first = await import("../../dist-electron/dialogLocations.js?first");
  assert.equal(first.lastDialogFolder("project-folder"), undefined, "nothing chosen yet: leave the choice to the OS");
  first.rememberDialogChoice("project-folder", chosen);
  assert.equal(first.lastDialogFolder("project-folder"), projects, "the parent shows the project's siblings");
  assert.equal(first.lastDialogFolder("attach-files"), undefined, "each kind of picker has its own memory");
  first.rememberDialogChoice("attach-files", path.join(projects, "notes.txt"));
  assert.equal(first.lastDialogFolder("attach-files"), projects);

  const afterRestart = await import("../../dist-electron/dialogLocations.js?restart");
  assert.equal(afterRestart.lastDialogFolder("project-folder"), projects);

  fs.rmSync(projects, { recursive: true });
  const afterDeletion = await import("../../dist-electron/dialogLocations.js?deleted");
  assert.equal(afterDeletion.lastDialogFolder("project-folder"), undefined, "a folder that no longer exists is not offered");
  afterDeletion.rememberDialogChoice("project-folder", undefined);
  afterDeletion.rememberDialogChoice("project-folder", "");
});
