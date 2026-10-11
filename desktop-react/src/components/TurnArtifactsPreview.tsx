import { ChevronRight, Eye, FileCode2, FileDiff } from "./icons";
import { useMemo } from "react";
import { canRenderArtifact } from "../artifactRenderers";
import type { TranscriptItem } from "../types/loom";
import { useRuntimeCopy } from "./runtimeCopy";
import "./turn-artifacts-preview.css";

type DiffFile = {
  path: string;
  displayPath: string;
  name: string;
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

function splitPatch(diff: string, fallbackPaths: string[]): Array<{ path: string; diff: string }> {
  const text = String(diff || "").replace(/\r\n/g, "\n");
  const fallback = fallbackPaths.map(normalizePath).filter(Boolean);
  if (!text.trim()) return fallback.map((path) => ({ path, diff: "" }));

  const lines = text.split("\n");
  const result: Array<{ path: string; diff: string }> = [];
  let currentPath = "";
  let current: string[] = [];

  const flush = () => {
    if (!current.length) return;
    result.push({
      path: currentPath || fallback[result.length] || fallback[0] || "Workspace change",
      diff: current.join("\n"),
    });
    current = [];
    currentPath = "";
  };

  for (const line of lines) {
    const gitHeader = /^diff --git a\/(.+?) b\/(.+)$/.exec(line);
    if (gitHeader) {
      flush();
      currentPath = normalizePath(gitHeader[2]);
      current.push(line);
      continue;
    }

    const newFileHeader = /^\+\+\+\s+(?:b\/)?(.+)$/.exec(line);
    if (!currentPath && newFileHeader && newFileHeader[1] !== "/dev/null") {
      currentPath = normalizePath(newFileHeader[1]);
    }
    current.push(line);
  }

  flush();
  return result.length ? result : [{ path: fallback[0] || "Workspace change", diff: text }];
}

function countPatch(diff: string): { additions: number; deletions: number } {
  let additions = 0;
  let deletions = 0;
  for (const line of String(diff || "").replace(/\r\n/g, "\n").split("\n")) {
    if (line.startsWith("+++") || line.startsWith("---")) continue;
    if (line.startsWith("+")) additions += 1;
    else if (line.startsWith("-")) deletions += 1;
  }
  return { additions, deletions };
}

/**
 * File edits are cumulative turn snapshots (TURN_DIFF_UPDATED carries the whole
 * turn's diff so far). A file's stats are those of the latest snapshot that
 * contains it; summing every snapshot counted early changes once per later edit.
 */
function collectFiles(items: TranscriptItem[]): DiffFile[] {
  const collected = new Map<string, { additions: number; deletions: number; editCount: number; chunk: string; lastChange: number }>();
  let snapshot = 0;

  for (const item of items) {
    if (item.type !== "file_edit") continue;
    snapshot += 1;
    const paths = (item.paths ?? []).map(normalizePath).filter(Boolean);
    const patches = splitPatch(String(item.diff ?? ""), paths);

    for (const patch of patches) {
      const path = normalizePath(patch.path) || paths[0] || "Workspace change";
      const previous = collected.get(path);
      const changed = !previous || previous.chunk !== patch.diff;
      collected.set(path, {
        ...countPatch(patch.diff),
        chunk: patch.diff,
        editCount: (previous?.editCount ?? 0) + (changed ? 1 : 0),
        lastChange: changed ? snapshot : previous.lastChange,
      });
    }

    for (const path of paths) {
      if (!collected.has(path)) {
        collected.set(path, { additions: 0, deletions: 0, editCount: 1, chunk: "", lastChange: snapshot });
      }
    }
  }

  return [...collected.entries()]
    .map(([path, value]) => ({
      path,
      displayPath: compactPath(path),
      name: basename(path),
      additions: value.additions,
      deletions: value.deletions,
      editCount: value.editCount,
      lastChange: value.lastChange,
    }))
    .sort((a, b) => a.displayPath.localeCompare(b.displayPath));
}

function openReview(path?: string): void {
  window.dispatchEvent(new CustomEvent("loom:review-open", {
    detail: path ? { path } : {},
  }));
}

/** Zero sides stay out of the way: "+4" rather than "+4 -0". */
function DiffStats({ additions, deletions, className }: { additions: number; deletions: number; className: string }) {
  if (!additions && !deletions) return null;
  return (
    <span className={className}>
      {additions ? <b>+{additions}</b> : null}
      {deletions ? <i>-{deletions}</i> : null}
    </span>
  );
}

export function TurnArtifactsPreview({ items, workspace }: { items: TranscriptItem[]; workspace?: string }) {
  const copy = useRuntimeCopy();
  const files = useMemo(() => collectFiles(items), [items]);
  const totals = useMemo(() => files.reduce(
    (total, file) => ({
      additions: total.additions + file.additions,
      deletions: total.deletions + file.deletions,
    }),
    { additions: 0, deletions: 0 },
  ), [files]);

  if (!files.length) return null;

  const primary = files[0];
  // The file the turn touched last is the one worth previewing.
  const previewFile = files
    .filter((file) => canRenderArtifact(file.path))
    .sort((a, b) => b.lastChange - a.lastChange)[0] ?? null;

  return (
    <section className="turn-artifacts rich-turn-artifacts review-summary-card" aria-label={copy.changedFilesRegion}>
      <button
        type="button"
        className="turn-artifacts-header review-summary-header"
        onClick={(event) => {
          event.stopPropagation();
          openReview(primary.path);
        }}
        title={copy.reviewTitle}
      >
        <span className="turn-artifacts-icon" aria-hidden="true"><FileDiff size={15} /></span>
        <span className="turn-artifacts-copy">
          <strong>{copy.changedFilesTitle(files.length, primary.name)}</strong>
          <DiffStats additions={totals.additions} deletions={totals.deletions} className="turn-artifacts-stats" />
        </span>
        <span className="turn-artifacts-action review-summary-action">
          <span>{copy.review}</span>
          <ChevronRight size={14} />
        </span>
      </button>

      <div className="turn-artifacts-file-strip turn-artifacts-files" aria-label={copy.changedFilesRegion}>
        {files.map((file) => (
          <button
            type="button"
            key={file.path}
            onClick={(event) => {
              event.stopPropagation();
              openReview(file.path);
            }}
            title={copy.openInReview(file.displayPath)}
          >
            <FileCode2 size={13.5} strokeWidth={1.8} />
            <code title={file.path}>{file.displayPath}</code>
            <DiffStats additions={file.additions} deletions={file.deletions} className="turn-artifacts-file-stats" />
          </button>
        ))}
      </div>

      {previewFile && workspace ? (
        <button
          type="button"
          className="turn-artifacts-preview-action"
          onClick={(event) => {
            event.stopPropagation();
            window.dispatchEvent(new CustomEvent("loom:artifact-preview-open", {
              detail: { path: previewFile.path, workspace },
            }));
          }}
          title={copy.previewArtifactTitle(previewFile.displayPath)}
        >
          <Eye size={13.5} strokeWidth={1.8} aria-hidden="true" />
          <span>{copy.previewArtifact(previewFile.name)}</span>
          <ChevronRight size={13} aria-hidden="true" />
        </button>
      ) : null}
    </section>
  );
}
