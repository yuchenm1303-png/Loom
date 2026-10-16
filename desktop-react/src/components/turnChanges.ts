/**
 * What a finished turn changed, from the `file_edit` snapshots it recorded: one entry per file, with what was done to it
 * and how many lines. No DOM and no copy; TurnArtifactsPreview.tsx draws it.
 */
import type { TranscriptItem } from "../types/loom";
import { splitDiffByFile } from "./diffFiles";
import type { ChangeKind } from "./runtimeCopy";

export type DiffFile = {
  path: string;
  displayPath: string;
  name: string;
  /** The folder the file sits in, as the card shows it ("" for a file at the root). */
  dir: string;
  kind: ChangeKind;
  additions: number;
  deletions: number;
  editCount: number;
  /** Order of the snapshot that last changed this file. */
  lastChange: number;
};

function normalizePath(value: string): string {
  return String(value || "")
    .trim()
    .replaceAll("\\", "/")
    .replace(/^[ab]\//, "")
    .replace(/^\.\//, "");
}

function compactPath(path: string): string {
  const normalized = normalizePath(path);
  const lower = normalized.toLowerCase();
  const loomMarker = "/loom/";
  const loomIndex = lower.lastIndexOf(loomMarker);
  if (loomIndex >= 0) return normalized.slice(loomIndex + loomMarker.length);
  const hiddenMarker = "/.loom/";
  const hiddenIndex = lower.lastIndexOf(hiddenMarker);
  if (hiddenIndex >= 0) return `.loom/${normalized.slice(hiddenIndex + hiddenMarker.length)}`;
  return normalized;
}

function basename(path: string): string {
  const normalized = normalizePath(path);
  return normalized.split("/").filter(Boolean).at(-1) || normalized || "Workspace change";
}

/**
 * One chunk of the turn's diff per file. The runtime's turn diff is plain `---`/`+++` pairs and never a `diff --git`
 * line (none of 563 recorded turn diffs has one), so splitting on git headers alone put every file's lines under the
 * first file's name and left the others at zero: the split has to understand both shapes.
 *
 * A chunk loses its trailing newline when another file follows it, and the last one keeps it; chunks are compared from
 * one snapshot to the next to see which files a snapshot changed, so the newline is not part of them.
 */
export function patchesOf(item: TranscriptItem, paths: string[]): Array<{ path: string; diff: string }> {
  const text = String(item.diff ?? "").replace(/\r\n/g, "\n");
  if (!text.trim()) return paths.map((path) => ({ path, diff: "" }));
  const chunks = [...splitDiffByFile(text)].map(([path, diff]) => ({ path: normalizePath(path), diff: diff.replace(/\n+$/, "") }));
  return chunks.length ? chunks : [{ path: paths[0] || "Workspace change", diff: text.replace(/\n+$/, "") }];
}

/** What the turn did to a file, from the header of its chunk: it had no old side, or no new one. */
export function kindOf(chunk: string): ChangeKind {
  const hunk = chunk.indexOf("\n@@");
  const header = hunk < 0 ? chunk : chunk.slice(0, hunk);
  if (/^--- \/dev\/null\s*$/m.test(header) || /^new file mode/m.test(header)) return "created";
  if (/^\+\+\+ \/dev\/null\s*$/m.test(header) || /^deleted file mode/m.test(header)) return "deleted";
  return "edited";
}

/** Lines added and removed in one file's chunk. From the first hunk on every line counts, so a removed `-- note` is not taken for a header. */
export function countPatch(chunk: string): { additions: number; deletions: number } {
  const lines = String(chunk || "").replace(/\r\n/g, "\n").split("\n");
  const hunk = lines.findIndex((line) => line.startsWith("@@"));
  let additions = 0;
  let deletions = 0;
  for (const line of hunk < 0 ? lines : lines.slice(hunk)) {
    if (hunk < 0 && (line.startsWith("+++") || line.startsWith("---"))) continue;
    if (line.startsWith("+")) additions += 1;
    else if (line.startsWith("-")) deletions += 1;
  }
  return { additions, deletions };
}

/**
 * File edits are cumulative turn snapshots (TURN_DIFF_UPDATED carries the whole
 * turn's diff so far). A file's stats are those of the latest snapshot that
 * contains it; summing every snapshot counted early changes once per later edit.
 *
 * The biggest change comes first: the card folds a long list, and the bars read as
 * one descending shape. Review opens on that file.
 */
export function collectFiles(items: TranscriptItem[]): DiffFile[] {
  const collected = new Map<string, { kind: ChangeKind; additions: number; deletions: number; editCount: number; chunk: string; lastChange: number }>();
  let snapshot = 0;

  for (const item of items) {
    if (item.type !== "file_edit") continue;
    snapshot += 1;
    const paths = (item.paths ?? []).map(normalizePath).filter(Boolean);

    for (const patch of patchesOf(item, paths)) {
      const path = patch.path || paths[0] || "Workspace change";
      const previous = collected.get(path);
      const changed = !previous || previous.chunk !== patch.diff;
      collected.set(path, {
        kind: kindOf(patch.diff),
        ...countPatch(patch.diff),
        chunk: patch.diff,
        editCount: (previous?.editCount ?? 0) + (changed ? 1 : 0),
        lastChange: changed ? snapshot : previous.lastChange,
      });
    }

    // A path the diff did not mention (or named by another spelling of the same file) still counts as changed.
    for (const path of paths) {
      if ([...collected.keys()].some((known) => known === path || known.endsWith(`/${path}`) || path.endsWith(`/${known}`))) continue;
      collected.set(path, { kind: "edited", additions: 0, deletions: 0, editCount: 1, chunk: "", lastChange: snapshot });
    }
  }

  return [...collected.entries()]
    .map(([path, value]) => {
      const displayPath = compactPath(path);
      const name = basename(path);
      return {
        path,
        displayPath,
        name,
        dir: displayPath.endsWith(`/${name}`) ? displayPath.slice(0, -(name.length + 1)) : "",
        kind: value.kind,
        additions: value.additions,
        deletions: value.deletions,
        editCount: value.editCount,
        lastChange: value.lastChange,
      };
    })
    .sort((a, b) => (b.additions + b.deletions) - (a.additions + a.deletions) || a.displayPath.localeCompare(b.displayPath));
}
