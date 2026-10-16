import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

// The logic behind the changed-files card (src/components/turnChanges.ts), without the card: what a turn did to which files.
const compile = (name) => ts.transpileModule(readFileSync(new URL(`../../src/components/${name}`, import.meta.url), "utf8"),
  { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const dataUrl = (code) => `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
const diffFilesUrl = dataUrl(compile("diffFiles.ts"));
const { collectFiles, countPatch, kindOf, patchesOf } = await import(dataUrl(compile("turnChanges.ts").replace('"./diffFiles"', `"${diffFilesUrl}"`)));

// The runtime's own shape (app/agent_runtime/diff_tracker.py, difflib.unified_diff): `---`/`+++` pairs, no `diff --git`,
// no old side for a created file, no new side for a deleted one.
const plain = (path, added, removed, kind) => {
  const lines = [...Array.from({ length: removed }, (_, i) => `-old ${i}`), ...Array.from({ length: added }, (_, i) => `+new ${i}`)];
  return [`--- ${kind === "new" ? "/dev/null" : `a/${path}`}`, `+++ ${kind === "gone" ? "/dev/null" : `b/${path}`}`,
    `@@ -${removed ? 1 : 0},${removed} +${added ? 1 : 0},${added} @@`, ...lines, ""].join("\n");
};
const git = (path, added, removed, kind) => [`diff --git a/${path} b/${path}`, ...(kind === "new" ? ["new file mode 100644"] : kind === "gone" ? ["deleted file mode 100644"] : []),
  plain(path, added, removed, kind)].join("\n");
const edit = (id, paths, diff) => ({ id, type: "file_edit", status: "completed", paths, diff });
const summary = (files) => files.map((file) => [file.path, file.kind, file.additions, file.deletions]);

for (const [shape, make] of [["the runtime's plain ---/+++ pairs", plain], ["diff --git sections", git]]) {
  test(`every file gets its own numbers from ${shape}`, () => {
    const diff = [make("src/a.ts", 3, 1), make("docs/new.md", 12, 0, "new"), make("old/gone.cfg", 0, 40, "gone")].join("");
    const files = collectFiles([edit("e", ["docs/new.md", "old/gone.cfg", "src/a.ts"], diff)]);
    // The biggest change first, whatever the order of the diff.
    assert.deepEqual(summary(files), [
      ["old/gone.cfg", "deleted", 0, 40],
      ["docs/new.md", "created", 12, 0],
      ["src/a.ts", "edited", 3, 1],
    ]);
  });
}

test("a turn's snapshots are cumulative: a file's numbers are those of the last snapshot that holds it", () => {
  const first = plain("src/a.ts", 2, 0);
  const second = first + plain("src/b.ts", 5, 5);
  const third = plain("src/a.ts", 9, 0) + plain("src/b.ts", 5, 5);
  const files = collectFiles([edit("1", ["src/a.ts"], first), edit("2", ["src/a.ts", "src/b.ts"], second), edit("3", ["src/a.ts", "src/b.ts"], third)]);
  assert.deepEqual(summary(files), [["src/b.ts", "edited", 5, 5], ["src/a.ts", "edited", 9, 0]]);
  // b was written in the second snapshot and left alone in the third; a was written in the first and again in the third.
  assert.deepEqual(files.map((file) => [file.path, file.editCount, file.lastChange]), [["src/b.ts", 1, 2], ["src/a.ts", 2, 3]]);
});

test("a created file stays created however often it is edited afterwards", () => {
  const files = collectFiles([edit("1", ["x.js"], plain("x.js", 10, 0, "new")), edit("2", ["x.js"], plain("x.js", 30, 0, "new"))]);
  assert.deepEqual(summary(files), [["x.js", "created", 30, 0]]);
});

test("a removed line that starts with dashes is a deletion, not a header", () => {
  // `-- note` removed from a SQL file is the diff line `--- note`.
  const diff = ["--- a/q.sql", "+++ b/q.sql", "@@ -1,2 +1,1 @@", "--- note", "-select 1;", "+select 2;", ""].join("\n");
  assert.deepEqual(countPatch(diff), { additions: 1, deletions: 2 });
  assert.deepEqual(summary(collectFiles([edit("1", ["q.sql"], diff)])), [["q.sql", "edited", 1, 2]]);
});

test("a diff with no headers at all still counts its lines, and carries the one path it is about", () => {
  const files = collectFiles([edit("1", ["docs/login.md"], "+# Login\n+Domain\n+OAuth\n-old")]);
  assert.deepEqual(summary(files), [["docs/login.md", "edited", 3, 1]]);
});

test("paths without a diff are still files the turn changed, once", () => {
  assert.deepEqual(summary(collectFiles([edit("1", ["a.txt", "b.txt"], "")])), [["a.txt", "edited", 0, 0], ["b.txt", "edited", 0, 0]]);
  // The same file named by an absolute path in `paths` and a relative one in the diff is one row, with the numbers.
  const files = collectFiles([edit("1", ["C:/Work/proj/src/a.ts"], plain("src/a.ts", 4, 2))]);
  assert.deepEqual(summary(files), [["src/a.ts", "edited", 4, 2]]);
});

test("windows line endings and a truncated diff change no count", () => {
  const diff = plain("src/a.ts", 3, 1).replace(/\n/g, "\r\n") + "\n... diff truncated ...\n";
  assert.deepEqual(summary(collectFiles([edit("1", ["src/a.ts"], diff)])), [["src/a.ts", "edited", 3, 1]]);
});

test("the card names a file by what is below the workspace, and keeps the folder apart", () => {
  const [file] = collectFiles([edit("1", ["C:/Workspace/Loom/services/gateway/prod/settings.yaml"], plain("C:/Workspace/Loom/services/gateway/prod/settings.yaml", 1, 0))]);
  assert.equal(file.displayPath, "services/gateway/prod/settings.yaml");
  assert.equal(file.name, "settings.yaml");
  assert.equal(file.dir, "services/gateway/prod");
  const [hidden] = collectFiles([edit("1", ["C:/Users/me/work/.loom/tmp/anew.js"], plain("C:/Users/me/work/.loom/tmp/anew.js", 79, 0, "new"))]);
  assert.equal(hidden.displayPath, ".loom/tmp/anew.js");
  assert.equal(hidden.dir, ".loom/tmp");
  const [root] = collectFiles([edit("1", ["package.json"], plain("package.json", 1, 1))]);
  assert.equal(root.dir, "");
});

test("equal changes keep a stable order, and the kind is read from the header only", () => {
  const diff = [plain("b.ts", 2, 0), plain("a.ts", 1, 1)].join("");
  assert.deepEqual(collectFiles([edit("1", ["a.ts", "b.ts"], diff)]).map((file) => file.path), ["a.ts", "b.ts"]);
  // A line inside a hunk that merely looks like a header does not make a file "created".
  assert.equal(kindOf(["--- a/x", "+++ b/x", "@@ -1,1 +1,1 @@", "--- /dev/null", "+x"].join("\n")), "edited");
  assert.equal(kindOf(["--- /dev/null", "+++ b/x", "@@ -0,0 +1,1 @@", "+x"].join("\n")), "created");
  assert.equal(kindOf(["diff --git a/x b/x", "deleted file mode 100644", "--- a/x", "+++ /dev/null", "@@ -1,1 +0,0 @@", "-x"].join("\n")), "deleted");
});

test("an item that is not a file edit, and an edit with nothing in it, give no files", () => {
  assert.deepEqual(collectFiles([{ id: "m", type: "assistant_message", text: "hi" }]), []);
  assert.deepEqual(collectFiles([edit("1", [], "")]), []);
  assert.deepEqual(patchesOf(edit("1", ["a"], ""), ["a"]), [{ path: "a", diff: "" }]);
});
